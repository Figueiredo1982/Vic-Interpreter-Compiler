"""
VIC - Victor Interpreter Compiler
Traduz uma linguagem simples (.vic) para assembly MIPS (MARS).

Suporta, por enquanto:
    input.int(X)        -> le um inteiro do usuario e guarda na variavel X
    input.str(H)         -> le uma string do usuario e guarda na variavel H
    int(X, 5)             -> atribui o valor literal 5 diretamente em X
    out.X                 -> imprime o valor guardado em X (int ou str)
    ADD(X,Y).Z            -> Z = X + Y  (Z pode ser igual a X ou Y; so para int)

Estrutura:
    SymbolTable   -> mapeia nome de variavel -> (tipo, offset ou label)
    CodeGen       -> gera os trechos de assembly para cada instrucao
    parse_line    -> reconhece uma linha de codigo .vic e devolve uma instrucao
    compile_vic   -> junta tudo: le o codigo .vic linha a linha e monta o .asm final
"""

import re
import os


# ---------------------------------------------------------------------------
# Tabela de simbolos
# ---------------------------------------------------------------------------
class SymbolTable:
    """
    Guarda duas coisas por variavel: o TIPO (int ou str) e a localizacao
    na memoria.

    - Variaveis 'int' compartilham um unico bloco 'data' e recebem um
      offset (0, 4, 8, ... bytes), igual a antes.
    - Variaveis 'str' recebem seu PROPRIO label/buffer (ex: buffer_H),
      porque uma string precisa de um espaco bem maior (ex: 1024 bytes)
      e e acessada byte a byte, nao como uma palavra de 4 bytes. Misturar
      string e int no mesmo bloco de offsets fixos faria a string
      estourar em cima da proxima variavel.

    Em ambos os casos: se a variavel ja existe, devolve a localizacao
    que ja tinha sido atribuida (e isso que faz um segundo
    input.int(X) ou input.str(H) sobrescrever em vez de criar nova).
    """

    STR_BUFFER_SIZE = 1024  # bytes reservados por variavel string

    def __init__(self):
        self._types = {}        # nome -> "int" | "str" | "array"
        self._int_offsets = {}  # nome -> offset em bytes dentro de 'data'
        self._str_labels = {}   # nome -> label do buffer (ex: "buffer_H")
        self._array_labels = {}  # nome -> label do vetor (ex: "array_vetor")
        self._array_sizes = {}   # nome -> tamanho (numero de inteiros)

    # --- inteiros ------------------------------------------------------
    def resolve_int(self, name: str) -> int:
        """Devolve o offset (em bytes) da variavel int, criando se necessario."""
        if name in self._types and self._types[name] != "int":
            raise TypeError(f"Variavel '{name}' ja foi usada como '{self._types[name]}', nao pode virar int")
        if name not in self._int_offsets:
            self._int_offsets[name] = len(self._int_offsets) * 4
            self._types[name] = "int"
        return self._int_offsets[name]

    def int_count(self) -> int:
        return len(self._int_offsets)

    # --- strings ---------------------------------------------------------
    def resolve_str(self, name: str) -> str:
        """Devolve o label do buffer da variavel string, criando se necessario."""
        if name in self._types and self._types[name] != "str":
            raise TypeError(f"Variavel '{name}' ja foi usada como '{self._types[name]}', nao pode virar str")
        if name not in self._str_labels:
            self._str_labels[name] = f"buffer_{name}"
            self._types[name] = "str"
        return self._str_labels[name]

    def str_labels_in_order(self):
        """Lista de (nome, label) na ordem de declaracao, para montar o .data."""
        return list(self._str_labels.items())

    # --- arrays ------------------------------------------------------------
    def declare_array(self, name: str, size: int) -> str:
        """Declara um novo array de 'size' inteiros, devolve seu label."""
        if name in self._types:
            raise TypeError(f"Variavel '{name}' ja foi declarada como '{self._types[name]}'")
        label = f"array_{name}"
        self._array_labels[name] = label
        self._array_sizes[name] = size
        self._types[name] = "array"
        return label

    def array_label(self, name: str) -> str:
        if name not in self._array_labels:
            raise NameError(f"Array '{name}' nao foi declarado (use int[]({name}, tamanho) antes)")
        return self._array_labels[name]

    def array_size(self, name: str) -> int:
        return self._array_sizes[name]

    def array_labels_in_order(self):
        """Lista de (nome, label, tamanho) na ordem de declaracao, para montar o .data."""
        return [(name, self._array_labels[name], self._array_sizes[name]) for name in self._array_labels]

    # --- consultas gerais ------------------------------------------------
    def exists(self, name: str) -> bool:
        return name in self._types

    def type_of(self, name: str) -> str:
        return self._types[name]


# ---------------------------------------------------------------------------
# Modulo Display (display de 7 segmentos memory-mapped do MARS)
# ---------------------------------------------------------------------------
# Enderecos fixos dos dois displays (memory-mapped I/O do MARS)
_DISPLAY_ADDR = {
    "Display1": "0xFFFF0011",  # display esquerdo
    "Display2": "0xFFFF0010",  # display direito
}

# letra do segmento -> indice do bit (a=0, b=1, ..., g=6, p=7)
_SEGMENT_BIT = {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4, "f": 5, "g": 6, "p": 7}

# tabela de padroes de 7 segmentos para os digitos 0-15 (hexadecimal),
# bit 0=a, 1=b, 2=c, 3=d, 4=e, 5=f, 6=g (segmento aceso = bit 1, ponto
# sempre apagado aqui). Convencao padrao de display catodo comum.
_SEG_TABLE = [
    0x3F,  # 0
    0x06,  # 1
    0x5B,  # 2
    0x4F,  # 3
    0x66,  # 4
    0x6D,  # 5
    0x7D,  # 6
    0x07,  # 7
    0x7F,  # 8
    0x6F,  # 9
    0x77,  # A (10)
    0x7C,  # b (11)
    0x39,  # C (12)
    0x5E,  # d (13)
    0x79,  # E (14)
    0x71,  # F (15)
]


# ---------------------------------------------------------------------------
# Modulo Teclado (Digital Lab Sim - scan_key / decode_key / wait_for_key)
# ---------------------------------------------------------------------------
# mapeia o "nome" da tecla, como escrito no .vic (0-9, A-F), para o
# codigo bruto de 8 bits que o teclado do Digital Lab Sim usa
_KEY_CODE = {
    "0": "0x81", "1": "0x11", "2": "0x12", "3": "0x14",
    "4": "0x21", "5": "0x22", "6": "0x24", "7": "0x41",
    "8": "0x42", "9": "0x44",
    "A": "0x18", "B": "0x28", "C": "0x48",
    "D": "0x88", "E": "0x84", "F": "0x82",
}

# Teclado visual do Digital Lab Sim:
#   0 1 2 3
#   4 5 6 7
#   8 9 A B
#   C D E F
# read_key retorna directamente o valor da tecla:
# tecla 0 -> indice 0, tecla 1 -> indice 1, ..., tecla A -> indice 10, etc.
_KEY_INDEX = {
    "0":  0, "1":  1, "2":  2, "3":  3,
    "4":  4, "5":  5, "6":  6, "7":  7,
    "8":  8, "9":  9, "A": 10, "B": 11,
    "C": 12, "D": 13, "E": 14, "F": 15,
}


# ---------------------------------------------------------------------------
# Modulo Bitmap (Bitmap Display do MARS)
# ---------------------------------------------------------------------------
_BITMAP_BASE   = "0x10010000"  # endereco base do Bitmap Display (512x256)
_BITMAP_WIDTH  = 512
_BITMAP_HEIGHT = 256

# padrao de token numerico: decimal (inteiro com sinal) OU hexadecimal (0x...)
# usado nos regex das instrucoes Bitmap que aceitam cores em hex
_NUM_PAT = r"(?:0[xX][0-9A-Fa-f]+|-?\d+|[A-Za-z_]\w*)"


# ---------------------------------------------------------------------------
# Geracao de codigo MIPS
# ---------------------------------------------------------------------------
class CodeGen:
    """
    Cada metodo aqui devolve um bloco de assembly MIPS (string) para uma
    instrucao da linguagem VIC. Os offsets ja vem resolvidos pela
    SymbolTable antes de chegar aqui - esta classe so sabe "gerar texto".
    """

    def __init__(self, symtab: SymbolTable):
        self.symtab = symtab
        self.lines = []  # acumula as linhas de .text geradas
        self._label_counter = 0  # usado para gerar labels unicos (FimIf_1, Else_1, ...)
        self._uses_mul = False   # True se MUL foi usado -> inclui rotina mul_segura
        self._uses_div = False   # True se DIV foi usado -> inclui rotina div_segura
        self._uses_display = False  # True se Display1/Display2 foi usado
        self._uses_keyboard = False  # True se [IMPOR] Teclado; e OnClick foram usados
        self._uses_strcmp = False  # True se alguma condicao comparou strings
        self._string_literals = {}  # texto (sem aspas) -> label, usado em condicoes tipo H == "QAT 01"
        self._uses_readfile = False  # True se READFILE foi usado
        self._uses_writefile = False  # True se WRITEFILE foi usado
        self._uses_strcpy = False  # True se str(H, "...") foi usado
        self._uses_bitmap = False  # True se [IMPOR] Bitmap; foi usado
        self._onclick_callbacks = []  # textos assembly dos callbacks de OnClick gerados
        self._onclick_data_decls = []  # declaracoes .data dos callbacks (memoria isolada)
        self._callkey_labels = {}  # tecla -> label do corpo do CALLKEY (ex: '1' -> 'callkey_1_body')
        self._function_exit_label = None  # se setado, 'return' salta para ca em vez de jr $ra direto

    def emit(self, text: str):
        self.lines.append(text)

    def _new_label(self, prefix: str) -> str:
        self._label_counter += 1
        return f"{prefix}_{self._label_counter}"

    # --- input.int(X) -----------------------------------------------------
    def gen_input_int(self, var: str):
        off = self.symtab.resolve_int(var)
        self.emit(f"""\
\t# input.int({var})
\tli $v0, 5
\tsyscall
\tmove $t0, $v0
\tla $s0, data
\tsw $t0, {off}($s0)""")

    # --- int(X, 5) -> atribui um valor literal direto na variavel ----------
    def gen_int_literal(self, var: str, value: int):
        off = self.symtab.resolve_int(var)
        self.emit(f"""\
\t# int({var},{value})
\tli $t5, {value}
\tla $s0, data
\tsw $t5, {off}($s0)""")

    # --- str(H, "QAT 01") -> copia a string literal para o buffer de H ------
    def gen_str_literal(self, var: str, text: str):
        """
        Atribui uma string literal a uma variavel str, usando strcpy para
        copiar o conteudo para dentro do buffer da variavel (buffer_H).
        Isso garante que out.H, strcmp, WRITEFILE, etc continuam
        funcionando sem nenhuma mudanca: o buffer ja contem a string pronta.
        """
        self._uses_strcpy = True
        buffer_label = self.symtab.resolve_str(var)
        str_label = self._string_literal_label(f'"{text}"')
        self.emit(f"""\
\t# str({var},"{text}")
\tla $a0, {buffer_label}
\tla $a1, {str_label}
\tjal strcpy""")

    # --- out.X (inteiro) -----------------------------------------------------
    def gen_out_int(self, var: str):
        off = self.symtab.resolve_int(var)
        self.emit(f"""\
\t# out.{var}
\tla $s0, data
\tlw $s1, {off}($s0)
\tmove $a0, $s1
\tli $v0, 1
\tsyscall""")

    # --- input.str(H) ----------------------------------------------------
    def gen_input_str(self, var: str):
        label = self.symtab.resolve_str(var)
        size = SymbolTable.STR_BUFFER_SIZE
        self.emit(f"""\
\t# input.str({var})
\tli $v0, 8
\tla $a0, {label}
\tli $a1, {size}
\tsyscall
\tla $t0, {label}
remove_nl_{var}:
\tlb $t1, 0($t0)
\tbeq $t1, 10, replace_nl_{var}
\tbeq $t1, 0, done_nl_{var}
\taddi $t0, $t0, 1
\tj remove_nl_{var}
replace_nl_{var}:
\tsb $zero, 0($t0)
done_nl_{var}:""")

    # --- out.H (string) --------------------------------------------------
    def gen_out_str(self, var: str):
        label = self.symtab.resolve_str(var)
        self.emit(f"""\
\t# out.{var}
\tli $v0, 4
\tla $a0, {label}
\tsyscall""")

    # --- ADD(X,Y).Z ----------------------------------------------------------
    def gen_add(self, x: str, y: str, dest: str):
        off_x = self.symtab.resolve_int(x)
        off_y = self.symtab.resolve_int(y)
        off_dest = self.symtab.resolve_int(dest)
        self.emit(f"""\
\t# ADD({x},{y}).{dest}
\tla $s0, data
\tlw $t1, {off_x}($s0)
\tlw $t2, {off_y}($s0)
\tadd $t1, $t1, $t2
\tsw $t1, {off_dest}($s0)""")

    # --- SUB(X,Y).Z ----------------------------------------------------------
    def gen_sub(self, x: str, y: str, dest: str):
        off_x = self.symtab.resolve_int(x)
        off_y = self.symtab.resolve_int(y)
        off_dest = self.symtab.resolve_int(dest)
        self.emit(f"""\
\t# SUB({x},{y}).{dest}  (X - Y)
\tla $s0, data
\tlw $t1, {off_x}($s0)
\tlw $t2, {off_y}($s0)
\tsub $t1, $t1, $t2
\tsw $t1, {off_dest}($s0)""")

    # --- return X (dentro de uma funcao) ----------------------------------
    def gen_return(self, value_token: str):
        if self._function_exit_label is None:
            raise SyntaxError("'return' usado fora de uma declaracao de funcao")
        load = self._load_operand(value_token, "$v0")
        self.emit(f"""\
\t# return {value_token}
{load}
\tj {self._function_exit_label}""")

    # --- MUL(X,Y).Z ------------------------------------------------------
    def gen_mul(self, x: str, y: str, dest: str):
        self._uses_mul = True
        off_x = self.symtab.resolve_int(x)
        off_y = self.symtab.resolve_int(y)
        off_dest = self.symtab.resolve_int(dest)
        err_label = self._new_label("MulErro")
        ok_label = self._new_label("MulOk")
        self.emit(f"""\
\t# MUL({x},{y}).{dest}
\tla $s0, data
\tlw $a0, {off_x}($s0)
\tlw $a1, {off_y}($s0)
\tjal mul_segura
\tbeqz $v1, {ok_label}
\tla $a0, msg_erro_mul_overflow
\tli $v0, 4
\tsyscall
\tli $v0, 10
\tsyscall
{ok_label}:
\tla $s0, data
\tsw $v0, {off_dest}($s0)""")

    # --- DIV(X,Y).Z ------------------------------------------------------
    def gen_div(self, x: str, y: str, dest: str):
        self._uses_div = True
        off_x = self.symtab.resolve_int(x)
        off_y = self.symtab.resolve_int(y)
        off_dest = self.symtab.resolve_int(dest)
        zero_label = self._new_label("DivErroZero")
        overflow_label = self._new_label("DivErroOverflow")
        ok_label = self._new_label("DivOk")
        self.emit(f"""\
\t# DIV({x},{y}).{dest}
\tla $s0, data
\tlw $a0, {off_x}($s0)
\tlw $a1, {off_y}($s0)
\tjal div_segura
\tbeq $v1, 1, {zero_label}
\tbeq $v1, 2, {overflow_label}
\tj {ok_label}
{zero_label}:
\tla $a0, msg_erro_div_zero
\tli $v0, 4
\tsyscall
\tli $v0, 10
\tsyscall
{overflow_label}:
\tla $a0, msg_erro_div_overflow
\tli $v0, 4
\tsyscall
\tli $v0, 10
\tsyscall
{ok_label}:
\tla $s0, data
\tsw $v0, {off_dest}($s0)""")

    # --- Display1.set(a, on/off) ------------------------------------------
    def gen_display_set(self, display: str, segment: str, state: str):
        self._uses_display = True
        addr = _DISPLAY_ADDR[display]
        bit = _SEGMENT_BIT[segment]
        on_off = 1 if state == "on" else 0
        self.emit(f"""\
\t# {display}.set({segment}, {state})
\tli $a0, {bit}
\tli $a1, {addr}
\tli $a2, {on_off}
\tjal set_seg""")

    # --- Display1.show_digit(N) -------------------------------------------
    def gen_display_show_digit(self, display: str, value_token: str):
        self._uses_display = True
        addr = _DISPLAY_ADDR[display]
        load = self._load_operand(value_token, "$a0")
        self.emit(f"""\
\t# {display}.show_digit({value_token})
{load}
\tli $a1, {addr}
\tjal show_digit""")

    # --- Display1.clear() --------------------------------------------------
    def gen_display_clear(self, display: str):
        self._uses_display = True
        addr = _DISPLAY_ADDR[display]
        self.emit(f"""\
\t# {display}.clear()
\tli $t6, {addr}
\tsb $zero, 0($t6)""")

    # --- Bitmap.set_pixel(x, y, cor) -----------------------------------------
    def gen_bitmap_set_pixel(self, x_tok: str, y_tok: str, cor_tok: str):
        self._uses_bitmap = True
        self.emit(f"""\
\t# Bitmap.set_pixel({x_tok}, {y_tok}, {cor_tok})
{self._load_operand(x_tok, "$a0")}
{self._load_operand(y_tok, "$a1")}
{self._load_operand(cor_tok, "$a2")}
\tjal set_pixel""")

    # --- Bitmap.fill_screen(cor) ---------------------------------------------
    def gen_bitmap_fill_screen(self, cor_tok: str):
        self._uses_bitmap = True
        self.emit(f"""\
\t# Bitmap.fill_screen({cor_tok})
{self._load_operand(cor_tok, "$a0")}
\tjal fill_screen""")

    # --- Bitmap.draw_square(x, y, tamanho, cor) ------------------------------
    def gen_bitmap_draw_square(self, x_tok: str, y_tok: str, size_tok: str, cor_tok: str):
        self._uses_bitmap = True
        self.emit(f"""\
\t# Bitmap.draw_square({x_tok}, {y_tok}, {size_tok}, {cor_tok})
{self._load_operand(x_tok, "$a0")}
{self._load_operand(y_tok, "$a1")}
{self._load_operand(size_tok, "$a2")}
{self._load_operand(cor_tok, "$a3")}
\tjal draw_square""")

    # --- Bitmap.clear_screen() -----------------------------------------------
    def gen_bitmap_clear_screen(self):
        self._uses_bitmap = True
        self.emit("""\
\t# Bitmap.clear_screen()
\tjal clear_screen""")

    # --- CALL(funcao, arg1, arg2, ...).dest ---------------------------------
    def gen_call(self, func_name: str, args: list, dest: str):
        """
        Carrega cada argumento (variavel int ou literal) no registrador
        $aN correspondente, chama a funcao via jal, e guarda o retorno
        ($v0) na variavel de destino.
        """
        arg_registers = ["$a0", "$a1", "$a2", "$a3"]
        lines = [f"\t# CALL({func_name}, {', '.join(args)}).{dest}"]
        for arg, reg in zip(args, arg_registers):
            lines.append(self._load_operand(arg, reg))
        lines.append(f"\tjal {func_name}")
        self.emit("\n".join(lines))

        off_dest = self.symtab.resolve_int(dest)
        self.emit(f"\tla $s0, data\n\tsw $v0, {off_dest}($s0)")

    # --- OnClick(N) { ... } (chamada bloqueante no fluxo principal) ---------
    def gen_onclick_call(self, key_name: str, callback_label: str):
        """
        Emite a chamada bloqueante a wait_for_key no ponto onde
        'OnClick(N) { ... }' aparece no programa: o fluxo para ali ate a
        tecla certa ser pressionada, executa o callback, e so entao
        continua para a proxima linha do .vic.
        """
        self._uses_keyboard = True
        key_index = _KEY_INDEX[key_name]
        self.emit(f"""\
\t# OnClick({key_name}) {{ ... }}
\tli $a0, {key_index}
\tla $a1, {callback_label}
\tjal wait_for_key""")

    # --- CALLKEY.(N)  (nao-bloqueante, usa dentro de While/For) ------------
    def gen_callkey_call(self, key_name: str):
        """
        Emite uma verificacao NAO-BLOQUEANTE da tecla usando read_key
        (com nop e keymap correta). Compara o INDICE retornado com o
        indice da tecla N. Se bater: chama wait_release (debounce) e
        depois o corpo. Se nao bater: pula e o loop continua normalmente.
        """
        self._uses_keyboard = True
        key_index = _KEY_INDEX[key_name]
        if key_name not in self._callkey_labels:
            raise NameError(
                f"'CALLKEY.({key_name})' usado sem 'Funct.CALLKEY.({key_name}){{...}}' "
                f"declarado antes no arquivo"
            )
        body_label = self._callkey_labels[key_name]
        skip_label = self._new_label(f"callkey_skip_{key_name}")
        self.emit(f"""\
\t# CALLKEY.({key_name})
\tjal read_key
\tli $t9, {key_index}
\tbne $v0, $t9, {skip_label}
\tjal wait_release
\tjal {body_label}
{skip_label}:""")

    # --- carrega um operando (variavel int ou literal numerico) em um registrador
    def _load_operand(self, token: str, reg: str) -> str:
        """
        Devolve as linhas de assembly que colocam o valor de 'token' (nome
        de variavel int, literal decimal '10'/'-3', ou literal hex '0xFF0000')
        dentro do registrador 'reg'.
        """
        # literal decimal (positivo ou negativo)
        if token.lstrip("-").isdigit():
            return f"\tli {reg}, {token}"
        # literal hexadecimal (ex: 0xFF0000 para cores do Bitmap)
        if token.lower().startswith("0x"):
            return f"\tli {reg}, {token}"
        # variavel int
        if not self.symtab.exists(token):
            raise NameError(f"Variavel '{token}' usada em condicao antes de ser definida")
        if self.symtab.type_of(token) != "int":
            raise TypeError(
                f"Nao e possivel comparar a variavel '{token}' (tipo "
                f"'{self.symtab.type_of(token)}') numa condicao de if; "
                f"apenas variaveis 'int' podem ser comparadas"
            )
        off = self.symtab.resolve_int(token)
        return f"\tla $s0, data\n\tlw {reg}, {off}($s0)"

    def _is_string_literal(self, token: str) -> bool:
        return token.startswith('"') and token.endswith('"')

    def _is_string_operand(self, token: str) -> bool:
        """True se o token e uma string literal, ou uma variavel do tipo str."""
        if self._is_string_literal(token):
            return True
        return self.symtab.exists(token) and self.symtab.type_of(token) == "str"

    def _string_literal_label(self, token: str) -> str:
        """
        Registra uma string literal usada numa condicao (ex: '\"QAT 01\"')
        e devolve o label .data correspondente, criando um novo na
        primeira vez que essa string exata aparece (reaproveita o label
        se o mesmo literal for usado de novo).
        """
        text = token[1:-1]  # remove as aspas
        if text not in self._string_literals:
            label = f"strlit_{len(self._string_literals)}"
            self._string_literals[text] = label
        return self._string_literals[text]

    def _load_string_address(self, token: str, reg: str) -> str:
        """Devolve as linhas que colocam o ENDERECO de uma string (literal ou variavel) em 'reg'."""
        if self._is_string_literal(token):
            label = self._string_literal_label(token)
            return f"\tla {reg}, {label}"
        label = self.symtab.resolve_str(token)
        return f"\tla {reg}, {label}"

    # --- if (cond) { ... } [else { ... }] -------------------------------------
    def gen_if_test(self, condition, fail_label: str, success_label: str = None):
        """
        Gera as comparacoes de uma condicao (lista de termos, do
        parse_condition). 'fail_label' e para onde pular se a condicao
        for FALSA (usado em AND e no caso simples). 'success_label', se
        fornecido, e para onde pular assim que uma condicao for VERDADEIRA
        (usado em OR, onde basta um termo verdadeiro para entrar no bloco).
        """
        is_or = any(conn == "||" for conn, *_ in condition)

        if is_or:
            # OR: cada termo verdadeiro pula direto para o bloco (success_label).
            # Se nenhum for verdadeiro, cai no fail_label apos testar todos.
            for _conn, left, op, right in condition:
                self._gen_one_test(left, op, right, fail_label=None, success_label=success_label)
            self.emit(f"\tj {fail_label}")
        else:
            # AND (ou termo unico): cada termo falso pula direto para fora
            # do bloco (fail_label). So entra no bloco se todos passarem.
            for _conn, left, op, right in condition:
                self._gen_one_test(left, op, right, fail_label=fail_label, success_label=None)

    def _gen_one_test(self, left, op, right, fail_label, success_label):
        """
        Gera o teste de UM termo da condicao. Exatamente um entre
        fail_label/success_label deve ser fornecido (o outro None),
        dependendo se estamos no modo AND (pula no fail) ou OR (pula no
        sucesso) - mesma convencao usada em gen_if_test.
        """
        is_string_cmp = self._is_string_operand(left) or self._is_string_operand(right)

        if is_string_cmp:
            if op not in ("==", "!="):
                raise TypeError(
                    f"Comparacao de string so suporta == e !=; '{op}' nao e valido "
                    f"para comparar strings"
                )
            if not (self._is_string_operand(left) and self._is_string_operand(right)):
                raise TypeError(
                    "Nao e possivel comparar uma string com um valor int na mesma condicao"
                )
            self.emit(self._load_string_address(left, "$a0"))
            self.emit(self._load_string_address(right, "$a1"))
            self.emit("\tjal strcmp")
            self._uses_strcmp = True
            # strcmp devolve $v0 = 1 se iguais, 0 se diferentes
            if success_label is not None:
                branch = "beq" if op == "==" else "bne"
                self.emit(f"\t{branch} $v0, 1, {success_label}")
            else:
                branch = "bne" if op == "==" else "beq"
                self.emit(f"\t{branch} $v0, 1, {fail_label}")
            return

        self.emit(self._load_operand(left, "$t3"))
        self.emit(self._load_operand(right, "$t4"))
        if success_label is not None:
            branch = _DIRECT_BRANCH[op]
            self.emit(f"\t{branch} $t3, $t4, {success_label}")
        else:
            branch = _INVERSE_BRANCH[op]
            self.emit(f"\t{branch} $t3, $t4, {fail_label}")

    def gen_if_else_skeleton(self, condition, has_else: bool):
        """
        Gera o "esqueleto" de saltos de um if/if-else e devolve os labels
        relevantes para quem for compilar o then_body/else_body:

            (label_else_ou_fim, label_fim)

        Quem chama deve:
          1. chamar isto,
          2. compilar o then_body,
          3. se has_else: emitir 'j label_fim', emitir 'label_else_ou_fim:',
             compilar o else_body,
          4. emitir 'label_fim:' (sempre).
        """
        is_or = any(conn == "||" for conn, *_ in condition)

        label_fim = self._new_label("FimIf")
        label_else = self._new_label("Else") if has_else else label_fim

        if is_or:
            label_then = self._new_label("BlocoIf")
            self.gen_if_test(condition, fail_label=label_else, success_label=label_then)
            self.emit(f"{label_then}:")
        else:
            self.gen_if_test(condition, fail_label=label_else)

        return label_else, label_fim

    # --- While(cond) { ... } ----------------------------------------------
    def gen_while_skeleton(self, condition):
        """
        Gera o "esqueleto" de um While: label de teste (volta-se aqui a
        cada iteracao), teste da condicao (pula para o fim se falsa), e
        devolve (label_teste, label_fim) para quem chamou:

          1. chamar isto (ja emite o label de teste e o teste em si),
          2. compilar o corpo do loop,
          3. emitir 'j label_teste' (volta para reavaliar a condicao),
          4. emitir 'label_fim:'.
        """
        is_or = any(conn == "||" for conn, *_ in condition)

        label_teste = self._new_label("WhileTeste")
        label_fim = self._new_label("FimWhile")

        self.emit(f"{label_teste}:")

        if is_or:
            label_corpo = self._new_label("WhileCorpo")
            self.gen_if_test(condition, fail_label=label_fim, success_label=label_corpo)
            self.emit(f"{label_corpo}:")
        else:
            self.gen_if_test(condition, fail_label=label_fim)

        return label_teste, label_fim

    # --- For(i, inicio, fim) { ... } ---------------------------------------
    def gen_for_skeleton(self, var_name: str, start_token: str, end_token: str):
        """
        Gera o "esqueleto" de um For(i, inicio, fim): inicializa i,
        testa i <= fim (label de teste, volta-se aqui a cada iteracao),
        e devolve (var_name, label_teste, label_fim) para quem chamou:

          1. chamar isto (ja inicializa i e emite o teste),
          2. compilar o corpo do loop,
          3. emitir 'i = i + 1' e 'j label_teste',
          4. emitir 'label_fim:'.

        O loop e INCLUSIVO no limite final: For(i, 0, 10) executa com
        i = 0, 1, 2, ..., 10 (11 iteracoes).
        """
        off_var = self.symtab.resolve_int(var_name)

        # inicializacao: i = inicio
        self.emit(f"\t# For({var_name}, {start_token}, {end_token})")
        init_load = self._load_operand(start_token, "$t5")
        self.emit(f"{init_load}\n\tla $s0, data\n\tsw $t5, {off_var}($s0)")

        label_teste = self._new_label("ForTeste")
        label_fim = self._new_label("FimFor")
        self.emit(f"{label_teste}:")

        # teste: se i > fim, sai (condicao inversa de i <= fim)
        end_load = self._load_operand(end_token, "$t4")
        self.emit(f"\tla $s0, data\n\tlw $t3, {off_var}($s0)\n{end_load}\n\tbgt $t3, $t4, {label_fim}")

        return var_name, label_teste, label_fim

    def gen_for_increment_and_jump(self, var_name: str, label_teste: str):
        """Emite 'i = i + 1' seguido do salto de volta ao teste do For."""
        off_var = self.symtab.resolve_int(var_name)
        self.emit(f"""\
\tla $s0, data
\tlw $t3, {off_var}($s0)
\taddi $t3, $t3, 1
\tsw $t3, {off_var}($s0)
\tj {label_teste}""")

    # --- SET(vetor, i, valor)  ->  vetor[i] = valor -------------------------
    def gen_array_set(self, array_name: str, index_token: str, value_token: str):
        label = self.symtab.array_label(array_name)
        index_load = self._load_operand(index_token, "$t6")
        value_load = self._load_operand(value_token, "$t7")
        self.emit(f"""\
\t# SET({array_name}, {index_token}, {value_token})
{index_load}
{value_load}
\tsll $t6, $t6, 2
\tla $t8, {label}
\tadd $t8, $t8, $t6
\tsw $t7, 0($t8)""")

    # --- GET(vetor, i).X  ->  X = vetor[i] -----------------------------------
    def gen_array_get(self, array_name: str, index_token: str, dest: str):
        label = self.symtab.array_label(array_name)
        index_load = self._load_operand(index_token, "$t6")
        off_dest = self.symtab.resolve_int(dest)
        self.emit(f"""\
\t# GET({array_name}, {index_token}).{dest}
{index_load}
\tsll $t6, $t6, 2
\tla $t8, {label}
\tadd $t8, $t8, $t6
\tlw $t7, 0($t8)
\tla $s0, data
\tsw $t7, {off_dest}($s0)""")

    # --- READFILE("nome.txt", buffer_var).N ---------------------------------
    def gen_readfile(self, filename: str, buffer_var: str, dest_count: str):
        self._uses_readfile = True
        filename_label = self._string_literal_label(f'"{filename}"')
        buffer_label = self.symtab.resolve_str(buffer_var)
        off_count = self.symtab.resolve_int(dest_count)
        self.emit(f"""\
\t# READFILE("{filename}", {buffer_var}).{dest_count}
\tla $a0, {filename_label}
\tla $a1, {buffer_label}
\tli $a2, {SymbolTable.STR_BUFFER_SIZE}
\tjal read_file
\tla $s0, data
\tsw $v0, {off_count}($s0)""")

    # --- WRITEFILE("nome.txt", string_var) ----------------------------------
    def gen_writefile(self, filename: str, string_var: str):
        self._uses_writefile = True
        filename_label = self._string_literal_label(f'"{filename}"')
        if not self.symtab.exists(string_var) or self.symtab.type_of(string_var) != "str":
            raise TypeError(f"WRITEFILE espera uma variavel str; '{string_var}' nao e str")
        buffer_label = self.symtab.resolve_str(string_var)
        self.emit(f"""\
\t# WRITEFILE("{filename}", {string_var})
\tla $a0, {filename_label}
\tla $a1, {buffer_label}
\tjal strlen
\tmove $a2, $v0
\tla $a0, {filename_label}
\tla $a1, {buffer_label}
\tjal write_file""")

    # --- monta o arquivo .asm final ------------------------------------------
    def build(self, extra_functions_asm=None, extra_data_decls=None) -> str:
        extra_functions_asm = extra_functions_asm or []
        extra_data_decls = extra_data_decls or []

        # bloco de inteiros: sempre existe, mesmo que vazio, para nao
        # quebrar 'la $s0, data' caso so existam variaveis string
        int_words = max(self.symtab.int_count(), 1)
        data_lines = [f"data: .space {int_words * 4}"]

        # um buffer separado para cada variavel string
        for _name, label in self.symtab.str_labels_in_order():
            data_lines.append(f"{label}: .space {SymbolTable.STR_BUFFER_SIZE}")

        # um bloco separado para cada array declarado (int[](nome, tamanho)),
        # 4 bytes por elemento, inicializado a zero
        for _name, label, size in self.symtab.array_labels_in_order():
            data_lines.append(f"{label}: .space {size * 4}")

        # strings literais usadas em condicoes (ex: if (H == "QAT 01"))
        for text, label in self._string_literals.items():
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            data_lines.append(f'{label}: .asciiz "{escaped}"')

        # blocos de dados das funcoes definidas pelo usuario (um por funcao,
        # ja que cada funcao tem seu proprio espaco de memoria para
        # parametros e variaveis locais)
        data_lines.extend(extra_data_decls)

        # blocos de dados dos callbacks de OnClick (mesma logica: memoria
        # isolada por callback)
        data_lines.extend(self._onclick_data_decls)

        # mensagens de erro de runtime, incluidas so se MUL/DIV forem usados
        if self._uses_mul:
            data_lines.append(
                'msg_erro_mul_overflow: .asciiz "Erro: overflow na multiplicacao\\n"'
            )
        if self._uses_div:
            data_lines.append(
                'msg_erro_div_zero: .asciiz "Erro: divisao por zero\\n"'
            )
            data_lines.append(
                'msg_erro_div_overflow: .asciiz "Erro: overflow na divisao\\n"'
            )
        # mensagem generica usada pelo tratador de excecoes de hardware
        if self._uses_mul or self._uses_div:
            data_lines.append(
                'msg_erro_excecao_hw: .asciiz "Erro: excecao aritmetica de hardware (overflow/div por zero)\\n"'
            )

        # tabela de padroes de 7 segmentos, usada por Display1/Display2.show_digit
        if self._uses_display:
            table_values = ", ".join(f"0x{v:02X}" for v in _SEG_TABLE)
            data_lines.append(f"seg_table: .byte {table_values}")

        # keymap do teclado: incluida sempre que o modulo Teclado e usado
        if self._uses_keyboard:
            data_lines.append(
                "vic_keymap: .byte "
                "0x11,0x21,0x41,0x81,"
                "0x12,0x22,0x42,0x82,"
                "0x14,0x24,0x44,0x84,"
                "0x18,0x28,0x48,0x88"
            )

        data_section = "\n\t".join(data_lines)
        body = "\n".join(self.lines)

        # rotinas seguras: ficam DEPOIS do corpo do main, isoladas, e so
        # sao alcancadas via 'jal' (nunca por fluxo sequencial, ja que o
        # main termina com syscall 10 antes de chegar nelas)
        routines = ""
        if self._uses_mul:
            routines += """

# =============================================
# Rotina: mul_segura
# Entrada: $a0 = multiplicando, $a1 = multiplicador
# Saida:   $v0 = resultado (32 bits validos)
#          $v1 = 0 se OK, 1 se houve overflow
# =============================================
mul_segura:
\tmult $a0, $a1
\tmfhi $t0
\tmflo $v0
\tsra  $t1, $v0, 31
\tbne  $t0, $t1, mul_overflow
\tli   $v1, 0
\tjr   $ra
mul_overflow:
\tli   $v1, 1
\tjr   $ra"""

        if self._uses_div:
            routines += """

# =============================================
# Rotina: div_segura
# Entrada: $a0 = dividendo, $a1 = divisor
# Saida:   $v0 = quociente (valido se $v1 == 0)
#          $v1 = 0 (OK), 1 (divisor zero), 2 (overflow)
# =============================================
div_segura:
\tbeq  $a1, $zero, div_zero
\tli   $t0, 0x80000000
\tbne  $a0, $t0, div_ok
\tli   $t0, -1
\tbne  $a1, $t0, div_ok
\tli   $v1, 2
\tli   $v0, 0
\tjr   $ra
div_zero:
\tli   $v1, 1
\tli   $v0, 0
\tjr   $ra
div_ok:
\tdiv  $a0, $a1
\tmflo $v0
\tmfhi $t0
\tli   $v1, 0
\tjr   $ra"""

        if self._uses_display:
            routines += """

# =============================================
# Rotina: set_seg
# Entrada: $a0 = indice do segmento (0=a, 1=b, ..., 6=g, 7=ponto)
#          $a1 = endereco do display (Display1 ou Display2)
#          $a2 = 1 para ligar, 0 para desligar
# Modifica somente o bit do segmento indicado, preservando os demais
# =============================================
set_seg:
\tli   $t0, 1
\tsllv $t0, $t0, $a0       # $t0 = mascara do segmento
\tlb   $t1, 0($a1)         # le byte atual do display
\tbeq  $a2, $zero, set_seg_desliga
\tor   $t1, $t1, $t0       # ligar: OR
\tsb   $t1, 0($a1)
\tjr   $ra
set_seg_desliga:
\tnor  $t0, $t0, $zero     # $t0 = NOT(mascara) (nor com zero = not)
\tand  $t1, $t1, $t0       # desligar: AND NOT(mascara)
\tsb   $t1, 0($a1)
\tjr   $ra

# =============================================
# Rotina: show_digit
# Entrada: $a0 = digito (0-15), $a1 = endereco do display
# Escreve o padrao de 7 segmentos completo de uma vez (seg_table)
# =============================================
show_digit:
\tla   $t0, seg_table
\tadd  $t0, $t0, $a0
\tlbu  $t1, 0($t0)
\tsb   $t1, 0($a1)
\tjr   $ra"""

        if self._uses_keyboard:
            routines += """

# =============================================
# read_key - Varre as 4 linhas e devolve o indice na vic_keymap
# Saida: $v0 = 0..15 ou -1 se nenhuma tecla premida
# O nop e essencial para o sinal do hardware estabilizar entre linhas
# =============================================
read_key:
	li   $t0, 0xFFFF0012
	li   $t1, 0xFFFF0014
	li   $t2, 1
rk_scan_row:
	sb   $t2, 0($t0)
	nop
	lbu  $t3, 0($t1)
	bnez $t3, rk_decode
	sll  $t2, $t2, 1
	blt  $t2, 16, rk_scan_row
	li   $v0, -1
	jr   $ra
rk_decode:
	la   $t4, vic_keymap
	li   $t5, 0
rk_search:
	lbu  $t6, 0($t4)
	beq  $t3, $t6, rk_found
	addiu $t4, $t4, 1
	addiu $t5, $t5, 1
	blt  $t5, 16, rk_search
	li   $v0, -1
	jr   $ra
rk_found:
	move $v0, $t5
	jr   $ra

# =============================================
# wait_release - Espera a tecla ser solta (debounce)
# =============================================
wait_release:
	li   $t0, 0xFFFF0014
wr_loop:
	lbu  $t1, 0($t0)
	bnez $t1, wr_loop
	jr   $ra"""

        if self._uses_strcmp:
            routines += """

# =============================================
# strcmp - Compara duas strings terminadas em zero
# Entrada: $a0 = endereco da string 1, $a1 = endereco da string 2
# Saida:   $v0 = 1 se forem iguais, 0 se forem diferentes
# =============================================
strcmp:
\taddi $sp, $sp, -4
\tsw   $ra, 0($sp)
strcmp_loop:
\tlb   $t0, 0($a0)
\tlb   $t1, 0($a1)
\tbne  $t0, $t1, strcmp_diferentes
\tbeq  $t0, $zero, strcmp_iguais
\taddi $a0, $a0, 1
\taddi $a1, $a1, 1
\tj    strcmp_loop
strcmp_iguais:
\tli   $v0, 1
\tj    strcmp_fim
strcmp_diferentes:
\tli   $v0, 0
strcmp_fim:
\tlw   $ra, 0($sp)
\taddi $sp, $sp, 4
\tjr   $ra"""

        if self._uses_readfile:
            routines += """

# =============================================
# read_file - Le o conteudo de um ficheiro para um buffer
# Entrada: $a0 = endereco do nome do ficheiro (.asciiz)
#          $a1 = endereco do buffer de destino
#          $a2 = tamanho maximo a ler (bytes)
# Saida:   $v0 = numero de bytes lidos (0=EOF, -1=erro)
# =============================================
read_file:
\taddi $sp, $sp, -16
\tsw   $ra, 12($sp)
\tsw   $s0, 8($sp)
\tsw   $s1, 4($sp)
\tsw   $s2, 0($sp)
\tmove $s0, $a0
\tmove $s1, $a1
\tmove $s2, $a2
\tli   $v0, 13
\tmove $a0, $s0
\tli   $a1, 0
\tli   $a2, 0
\tsyscall
\tbltz $v0, rf_error
\tmove $t0, $v0
\tli   $v0, 14
\tmove $a0, $t0
\tmove $a1, $s1
\tmove $a2, $s2
\tsyscall
\tmove $s2, $v0
\tbltz $s2, rf_close_error
\tli   $v0, 16
\tmove $a0, $t0
\tsyscall
\tmove $v0, $s2
\tj    rf_exit
rf_close_error:
\tli   $v0, 16
\tmove $a0, $t0
\tsyscall
rf_error:
\tli   $v0, -1
rf_exit:
\tlw   $ra, 12($sp)
\tlw   $s0, 8($sp)
\tlw   $s1, 4($sp)
\tlw   $s2, 0($sp)
\taddi $sp, $sp, 16
\tjr   $ra"""

        if self._uses_writefile:
            routines += """

# =============================================
# strlen - Calcula o comprimento de uma string (ate ao \\0)
# Entrada: $a0 = endereco da string
# Saida:   $v0 = numero de caracteres (sem o \\0)
# =============================================
strlen:
\tli   $v0, 0
strlen_loop:
\tlb   $t0, 0($a0)
\tbeqz $t0, strlen_end
\taddi $a0, $a0, 1
\taddi $v0, $v0, 1
\tj    strlen_loop
strlen_end:
\tjr   $ra

# =============================================
# write_file - Escreve uma string para um ficheiro
# Entrada: $a0 = nome do ficheiro (.asciiz)
#          $a1 = endereco da string a escrever
#          $a2 = numero de bytes a escrever
# Saida:   $v0 = 0 se OK, -1 se erro
# =============================================
write_file:
\taddi $sp, $sp, -16
\tsw   $ra, 12($sp)
\tsw   $s0, 8($sp)
\tsw   $s1, 4($sp)
\tsw   $s2, 0($sp)
\tmove $s0, $a0
\tmove $s1, $a1
\tmove $s2, $a2
\tli   $v0, 13
\tmove $a0, $s0
\tli   $a1, 9
\tli   $a2, 0
\tsyscall
\tbltz $v0, wf_error
\tmove $t0, $v0
\tli   $v0, 15
\tmove $a0, $t0
\tmove $a1, $s1
\tmove $a2, $s2
\tsyscall
\tbltz $v0, wf_close_error
\tli   $v0, 16
\tmove $a0, $t0
\tsyscall
\tli   $v0, 0
\tj    wf_exit
wf_close_error:
\tli   $v0, 16
\tmove $a0, $t0
\tsyscall
wf_error:
\tli   $v0, -1
wf_exit:
\tlw   $ra, 12($sp)
\tlw   $s0, 8($sp)
\tlw   $s1, 4($sp)
\tlw   $s2, 0($sp)
\taddi $sp, $sp, 16
\tjr   $ra"""

        if self._uses_strcpy:
            routines += """

# =============================================
# strcpy - Copia string terminada em \\0 (usada por str(H, "..."))
# Entrada: $a0 = destino, $a1 = fonte
# =============================================
strcpy:
\tlb   $t0, 0($a1)
\tsb   $t0, 0($a0)
\tbeq  $t0, $zero, strcpy_fim
\taddi $a0, $a0, 1
\taddi $a1, $a1, 1
\tj    strcpy
strcpy_fim:
\tjr   $ra"""

        if self._uses_bitmap:
            routines += f"""

# =============================================================
# Biblioteca Bitmap para MARS (512x256, base {_BITMAP_BASE})
# =============================================================

# --- dados do Bitmap (injetados no .text como labels locais) ---
BITMAP_BASE:
\t.word {_BITMAP_BASE}
SCREEN_WIDTH:
\t.word {_BITMAP_WIDTH}
SCREEN_HEIGHT:
\t.word {_BITMAP_HEIGHT}

# -------------------------------------------------------------
# set_pixel: acende um pixel em (x, y) com a cor dada
#   $a0 = x, $a1 = y, $a2 = cor (0x00RRGGBB)
# -------------------------------------------------------------
set_pixel:
\tlw    $t0, BITMAP_BASE
\tlw    $t1, SCREEN_WIDTH
\tmul   $t2, $a0, 4
\tmul   $t3, $t1, 4
\tmul   $t3, $t3, $a1
\tadd   $t4, $t0, $t2
\tadd   $t4, $t4, $t3
\tsw    $a2, 0($t4)
\tjr    $ra

# -------------------------------------------------------------
# fill_screen: preenche toda a tela com a cor
#   $a0 = cor
# -------------------------------------------------------------
fill_screen:
\tlw    $t0, BITMAP_BASE
\tlw    $t1, SCREEN_WIDTH
\tlw    $t2, SCREEN_HEIGHT
\tli    $t3, 0
fs_loop_y:
\tbge   $t3, $t2, fs_done
\tli    $t4, 0
fs_loop_x:
\tbge   $t4, $t1, fs_next_y
\tmul   $t5, $t4, 4
\tmul   $t6, $t1, 4
\tmul   $t6, $t6, $t3
\tadd   $t7, $t0, $t5
\tadd   $t7, $t7, $t6
\tsw    $a0, 0($t7)
\taddi  $t4, $t4, 1
\tj     fs_loop_x
fs_next_y:
\taddi  $t3, $t3, 1
\tj     fs_loop_y
fs_done:
\tjr    $ra

# -------------------------------------------------------------
# draw_square: quadrado preenchido N x N
#   $a0 = x, $a1 = y, $a2 = tamanho N, $a3 = cor
# -------------------------------------------------------------
draw_square:
\tlw    $t0, BITMAP_BASE
\tlw    $t1, SCREEN_WIDTH
\tmove  $t2, $a1
\tadd   $t3, $a1, $a2
ds_loop_y:
\tbge   $t2, $t3, ds_done
\tmove  $t4, $a0
\tadd   $t5, $a0, $a2
ds_loop_x:
\tbge   $t4, $t5, ds_next_y
\tmul   $t6, $t4, 4
\tmul   $t7, $t1, 4
\tmul   $t7, $t7, $t2
\tadd   $t8, $t0, $t6
\tadd   $t8, $t8, $t7
\tsw    $a3, 0($t8)
\taddi  $t4, $t4, 1
\tj     ds_loop_x
ds_next_y:
\taddi  $t2, $t2, 1
\tj     ds_loop_y
ds_done:
\tjr    $ra

# -------------------------------------------------------------
# clear_screen: limpa a tela (preto)
# -------------------------------------------------------------
clear_screen:
\tli    $a0, 0x00000000
\tj     fill_screen"""

        # tratador de excecoes de hardware (.ktext): segunda camada de
        # protecao, caso uma instrucao perigosa (mult/div fora das rotinas
        # seguras, ou overflow de 'add') dispare excecao diretamente.
        # So incluido se MUL ou DIV forem usados no programa.
        ktext_section = ""
        if self._uses_mul or self._uses_div:
            ktext_section = """

.ktext 0x80000180
\tmfc0 $k0, $13          # Cause register
\tsrl  $k0, $k0, 2
\tandi $k0, $k0, 0x1f    # codigo da excecao
\tli   $a0, 12           # Integer Overflow
\tbeq  $k0, $a0, excecao_tratada
\tli   $a0, 15           # Trap (ex: div por zero gerado por 'div'/'break')
\tbeq  $k0, $a0, excecao_tratada
\t# excecao nao tratada por nos: deixa o MARS reportar normalmente
\tli $v0, 10
\tsyscall
excecao_tratada:
\tla $a0, msg_erro_excecao_hw
\tli $v0, 4
\tsyscall
\tli $v0, 10
\tsyscall"""

        # funcoes definidas pelo usuario: ficam DEPOIS do corpo do main e
        # ANTES das rotinas internas (mul_segura, etc), tambem isoladas -
        # so alcancadas via 'jal', nunca por fluxo sequencial
        user_functions_section = ""
        for func_text in extra_functions_asm:
            user_functions_section += f"\n\n{func_text}"

        # callbacks de OnClick: mesma logica, so alcancados via 'jalr'
        # dentro de wait_for_key, nunca por fluxo sequencial
        for cb_text in self._onclick_callbacks:
            user_functions_section += f"\n\n{cb_text}"

        # se o corpo do "main" estiver vazio, este arquivo .vic so contem
        # declaracoes de funcao (nenhuma instrucao solta no nivel
        # principal) - ou seja, e um MODULO/BIBLIOTECA, nao um programa
        # com ponto de entrada proprio. Nesse caso, NAO geramos 'main:'
        # nem '.globl main': dois arquivos com 'main:' dariam erro de
        # "simbolo redefinido" quando montados juntos no MARS. O modulo
        # so expoe suas funcoes (.globl em cada uma), para serem chamadas
        # via 'jal' de outro arquivo .asm que tenha o 'main:' de verdade.
        is_library_only = not body.strip()

        if is_library_only:
            globl_lines = "\n".join(
                f".globl {decl.split(':')[0]}"
                for decl in (extra_functions_asm + self._onclick_callbacks)
            )
            return f""".data
\t{data_section}

.text
{globl_lines}{user_functions_section}{routines}{ktext_section}
"""

        return f""".data
\t{data_section}

.text
.globl main
main:
{body}

\tli $v0, 10
\tsyscall{user_functions_section}{routines}{ktext_section}
"""


# ---------------------------------------------------------------------------
# Parser de condicoes (usado dentro de if (...))
# ---------------------------------------------------------------------------
# Operador relacional -> (instrucao MIPS que salta quando a condicao e FALSA,
# ou seja, a condicao "contraria" usada para pular o bloco do if)
_INVERSE_BRANCH = {
    "==": "bne",   # se NAO for igual, pula
    "!=": "beq",   # se for igual, pula
    "<":  "bge",   # se NAO for menor (>=), pula
    ">":  "ble",   # se NAO for maior (<=), pula
    "<=": "bgt",   # se NAO for <=  (>), pula
    ">=": "blt",   # se NAO for >=  (<), pula
}

# Operador relacional -> instrucao MIPS que salta quando a condicao e VERDADEIRA
# (usado no OR, onde qualquer condicao verdadeira deve pular direto pro bloco)
_DIRECT_BRANCH = {
    "==": "beq",
    "!=": "bne",
    "<":  "blt",
    ">":  "bgt",
    "<=": "ble",
    ">=": "bge",
}

RE_SIMPLE_COND = re.compile(
    r'^\s*([A-Za-z_]\w*)\s*(==|!=|<=|>=|<|>)\s*("[^"]*"|[A-Za-z_]\w*|-?\d+)\s*$'
)


def parse_condition(cond_text: str):
    """
    Recebe o texto dentro dos parenteses de um if, ex: 'X == Y' ou
    'X < Y && A == B', e devolve uma lista de "termos":

        [(conector_ou_None, var_esquerda, operador, lado_direito), ...]

    conector e None para o primeiro termo, e '&&' ou '||' para os
    seguintes. Nao aceita misturar && e || na mesma condicao (similar
    a maioria das linguagens, que exigem parenteses nesse caso - aqui
    simplesmente nao suportamos ainda, para manter o parser simples).
    """
    if "&&" in cond_text and "||" in cond_text:
        raise SyntaxError(
            "Misturar && e || na mesma condicao ainda nao e suportado; "
            "separe em ifs aninhados"
        )

    if "&&" in cond_text:
        connector = "&&"
        parts = cond_text.split("&&")
    elif "||" in cond_text:
        connector = "||"
        parts = cond_text.split("||")
    else:
        connector = None
        parts = [cond_text]

    terms = []
    for i, part in enumerate(parts):
        m = RE_SIMPLE_COND.match(part)
        if not m:
            raise SyntaxError(f"Condicao mal formada: {part.strip()!r}")
        left, op, right = m.group(1), m.group(2), m.group(3)
        terms.append((connector if i > 0 else None, left, op, right))

    return terms


# ---------------------------------------------------------------------------
# Parser: reconhece uma linha .vic e devolve (instrucao, args)
# ---------------------------------------------------------------------------
RE_INPUT_INT = re.compile(r"^input\.int\(\s*([A-Za-z_]\w*)\s*\)$")
RE_INPUT_STR = re.compile(r"^input\.str\(\s*([A-Za-z_]\w*)\s*\)$")
RE_OUT = re.compile(r"^out\.([A-Za-z_]\w*)$")
RE_ADD = re.compile(
    r"^ADD\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)\.([A-Za-z_]\w*)$"
)
RE_SUB = re.compile(
    r"^SUB\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)\.([A-Za-z_]\w*)$"
)
RE_MUL = re.compile(
    r"^MUL\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)\.([A-Za-z_]\w*)$"
)
RE_DIV = re.compile(
    r"^DIV\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)\.([A-Za-z_]\w*)$"
)
RE_INT_LITERAL = re.compile(
    r"^int\(\s*([A-Za-z_]\w*)\s*,\s*(-?\d+)\s*\)$"
)
# str(H, "QAT 01")  -> atribui a string literal "QAT 01" diretamente a H
RE_STR_LITERAL = re.compile(
    r'^str\(\s*([A-Za-z_]\w*)\s*,\s*"([^"]*)"\s*\)$'
)
RE_IF_OPEN = re.compile(r"^if\s*\((.+)\)\s*\{$")
RE_ELSE_OPEN = re.compile(r"^\}\s*else\s*\{$")
RE_BLOCK_CLOSE = re.compile(r"^\}$")
RE_WHILE_OPEN = re.compile(r"^While\s*\((.+)\)\s*\{$")
# For(i, 0, 10) {   -> i vai de 0 ate 10 (inclusive), passo +1
RE_FOR_OPEN = re.compile(
    r"^For\(\s*([A-Za-z_]\w*)\s*,\s*(-?\d+|[A-Za-z_]\w*)\s*,\s*(-?\d+|[A-Za-z_]\w*)\s*\)\s*\{$"
)

# --- arrays ---------------------------------------------------------------
# int[](vetor, 100)  -> declara um array de 100 inteiros
RE_ARRAY_DECLARE = re.compile(r"^int\[\]\(\s*([A-Za-z_]\w*)\s*,\s*(\d+)\s*\)$")
# SET(vetor, i, valor)  -> vetor[i] = valor
RE_ARRAY_SET = re.compile(
    r"^SET\(\s*([A-Za-z_]\w*)\s*,\s*(-?\d+|[A-Za-z_]\w*)\s*,\s*(-?\d+|[A-Za-z_]\w*)\s*\)$"
)
# GET(vetor, i).X  -> X = vetor[i]
RE_ARRAY_GET = re.compile(
    r"^GET\(\s*([A-Za-z_]\w*)\s*,\s*(-?\d+|[A-Za-z_]\w*)\s*\)\.([A-Za-z_]\w*)$"
)

# --- arquivos .txt ---------------------------------------------------------
# READFILE("numeros.txt", buffer_var).N  -> le o arquivo para buffer_var (str),
# guarda em N quantos bytes foram lidos (int)
RE_READFILE = re.compile(
    r'^READFILE\(\s*"([^"]+)"\s*,\s*([A-Za-z_]\w*)\s*\)\.([A-Za-z_]\w*)$'
)
# WRITEFILE("saida.txt", string_var)  -> escreve string_var no arquivo (sobrescreve)
RE_WRITEFILE = re.compile(
    r'^WRITEFILE\(\s*"([^"]+)"\s*,\s*([A-Za-z_]\w*)\s*\)$'
)

# --- funcoes definidas pelo usuario --------------------------------------
# funct.(a,b).hello{   -> abre a declaracao da funcao 'hello' com parametros a,b
RE_FUNC_OPEN = re.compile(
    r"^funct\.\(\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*\)\.([A-Za-z_]\w*)\{$"
)
RE_RETURN = re.compile(r"^return\s+([A-Za-z_]\w*|-?\d+)$")
# CALL(hello, X, Y).Z  -> chama hello(X,Y), guarda retorno em Z
RE_CALL = re.compile(
    r"^CALL\(\s*([A-Za-z_]\w*)\s*,\s*(.+)\)\.([A-Za-z_]\w*)$"
)

# --- modulo Display -----------------------------------------------------
RE_IMPORT = re.compile(r"^\[IMPOR\]\s*([A-Za-z_]\w*)\s*;$")
# [IMPOR] : Soma.asm;  -> importa o arquivo externo Soma.asm (gerado a
# partir de Soma.vic), disponibilizando suas funcoes com o prefixo Soma.
RE_IMPORT_FILE = re.compile(r"^\[IMPOR\]\s*:\s*([A-Za-z_]\w*)\.asm\s*;$")
# IMPORT.Soma.soma(x,y).H  -> chama a funcao 'soma' do modulo 'Soma'
RE_IMPORT_CALL = re.compile(
    r"^IMPORT\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)\(\s*(.*)\)\.([A-Za-z_]\w*)$"
)
RE_DISPLAY_SET = re.compile(
    r"^(Display[12])\.set\(\s*([a-gp])\s*,\s*(on|off)\s*\)$"
)
RE_DISPLAY_SHOW_DIGIT = re.compile(
    r"^(Display[12])\.show_digit\(\s*([A-Za-z_]\w*|\d+)\s*\)$"
)
RE_DISPLAY_CLEAR = re.compile(r"^(Display[12])\.clear\(\)$")

# --- modulo Bitmap --------------------------------------------------------
# token numerico: hex (0x...) ou decimal ou variavel
_N = r"(?:0[xX][0-9A-Fa-f]+|-?\d+|[A-Za-z_]\w*)"
RE_BITMAP_SET_PIXEL = re.compile(
    rf"^Bitmap\.set_pixel\(\s*({_N})\s*,\s*({_N})\s*,\s*({_N})\s*\)$"
)
RE_BITMAP_FILL = re.compile(
    rf"^Bitmap\.fill_screen\(\s*({_N})\s*\)$"
)
RE_BITMAP_DRAW_SQUARE = re.compile(
    rf"^Bitmap\.draw_square\(\s*({_N})\s*,\s*({_N})\s*,\s*({_N})\s*,\s*({_N})\s*\)$"
)
RE_BITMAP_CLEAR = re.compile(r"^Bitmap\.clear_screen\(\)$")

# --- modulo Teclado -------------------------------------------------------
# OnClick(1) {   -> abre um callback bloqueante para a tecla '1' (ou A-F)
RE_ONCLICK_OPEN = re.compile(r"^OnClick\(\s*([0-9A-Fa-f])\s*\)\s*\{$")

# --- CALLKEY (nao-bloqueante, para usar dentro de While/For) -------------
# Funct.CALLKEY.(1){   -> define o bloco da tecla '1'
RE_CALLKEY_DEF = re.compile(r"^Funct\.CALLKEY\.\(\s*([0-9A-Fa-f])\s*\)\s*\{$")
# CALLKEY.(1)          -> no fluxo: se tecla '1' estiver pressionada, executa o bloco
RE_CALLKEY_USE = re.compile(r"^CALLKEY\.\(\s*([0-9A-Fa-f])\s*\)$")


def parse_line(line: str):
    """
    Recebe uma linha de codigo .vic (ja sem comentarios/espacos nas pontas)
    e devolve uma tupla (tipo_instrucao, *args), ou None se a linha for
    vazia/comentario.

    Para instrucoes "estruturais" (if / else / fechamento de bloco), devolve
    marcadores especiais que sao consumidos por parse_block, nao por
    compile_block diretamente.
    """
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("'"):
        return None

    m = RE_IF_OPEN.match(line)
    if m:
        return ("if_open", m.group(1))

    if RE_ELSE_OPEN.match(line):
        return ("else_open",)

    if RE_BLOCK_CLOSE.match(line):
        return ("block_close",)

    m = RE_WHILE_OPEN.match(line)
    if m:
        return ("while_open", m.group(1))

    m = RE_FOR_OPEN.match(line)
    if m:
        return ("for_open", m.group(1), m.group(2), m.group(3))

    m = RE_FUNC_OPEN.match(line)
    if m:
        params = [p.strip() for p in m.group(1).split(",")]
        func_name = m.group(2)
        return ("func_open", func_name, params)

    m = RE_ONCLICK_OPEN.match(line)
    if m:
        key_name = m.group(1).upper()
        return ("onclick_open", key_name)

    m = RE_CALLKEY_DEF.match(line)
    if m:
        return ("callkey_def_open", m.group(1).upper())

    m = RE_CALLKEY_USE.match(line)
    if m:
        return ("callkey_use", m.group(1).upper())

    m = RE_RETURN.match(line)
    if m:
        return ("return", m.group(1))

    m = RE_CALL.match(line)
    if m:
        func_name = m.group(1)
        args = [a.strip() for a in m.group(2).split(",")]
        dest = m.group(3)
        return ("call", func_name, args, dest)

    m = RE_IMPORT_FILE.match(line)
    if m:
        return ("import_file", m.group(1))

    m = RE_IMPORT_CALL.match(line)
    if m:
        module_name = m.group(1)
        func_name = m.group(2)
        args_text = m.group(3).strip()
        args = [a.strip() for a in args_text.split(",")] if args_text else []
        dest = m.group(4)
        return ("import_call", module_name, func_name, args, dest)

    m = RE_IMPORT.match(line)
    if m:
        return ("import", m.group(1))

    m = RE_DISPLAY_SET.match(line)
    if m:
        return ("display_set", m.group(1), m.group(2), m.group(3))

    m = RE_DISPLAY_SHOW_DIGIT.match(line)
    if m:
        return ("display_show_digit", m.group(1), m.group(2))

    m = RE_DISPLAY_CLEAR.match(line)
    if m:
        return ("display_clear", m.group(1))

    m = RE_BITMAP_SET_PIXEL.match(line)
    if m:
        return ("bitmap_set_pixel", m.group(1), m.group(2), m.group(3))

    m = RE_BITMAP_FILL.match(line)
    if m:
        return ("bitmap_fill_screen", m.group(1))

    m = RE_BITMAP_DRAW_SQUARE.match(line)
    if m:
        return ("bitmap_draw_square", m.group(1), m.group(2), m.group(3), m.group(4))

    if RE_BITMAP_CLEAR.match(line):
        return ("bitmap_clear_screen",)

    m = RE_INPUT_INT.match(line)
    if m:
        return ("input_int", m.group(1))

    m = RE_INPUT_STR.match(line)
    if m:
        return ("input_str", m.group(1))

    m = RE_ARRAY_DECLARE.match(line)
    if m:
        return ("array_declare", m.group(1), int(m.group(2)))

    m = RE_ARRAY_SET.match(line)
    if m:
        return ("array_set", m.group(1), m.group(2), m.group(3))

    m = RE_ARRAY_GET.match(line)
    if m:
        return ("array_get", m.group(1), m.group(2), m.group(3))

    m = RE_READFILE.match(line)
    if m:
        return ("readfile", m.group(1), m.group(2), m.group(3))

    m = RE_WRITEFILE.match(line)
    if m:
        return ("writefile", m.group(1), m.group(2))

    m = RE_STR_LITERAL.match(line)
    if m:
        return ("str_literal", m.group(1), m.group(2))

    m = RE_INT_LITERAL.match(line)
    if m:
        return ("int_literal", m.group(1), int(m.group(2)))

    m = RE_OUT.match(line)
    if m:
        return ("out", m.group(1))

    m = RE_ADD.match(line)
    if m:
        return ("add", m.group(1), m.group(2), m.group(3))

    m = RE_SUB.match(line)
    if m:
        return ("sub", m.group(1), m.group(2), m.group(3))

    m = RE_MUL.match(line)
    if m:
        return ("mul", m.group(1), m.group(2), m.group(3))

    m = RE_DIV.match(line)
    if m:
        return ("div", m.group(1), m.group(2), m.group(3))

    raise SyntaxError(f"Linha .vic nao reconhecida: {line!r}")


def parse_block(lines, start=0, _top_level=True):
    """
    Le 'lines' (lista de instrucoes ja tokenizadas por parse_line, incluindo
    marcadores estruturais como if_open/else_open/block_close) a partir do
    indice 'start' ate encontrar um 'block_close' que feche o bloco atual.

    Devolve (arvore_de_instrucoes, indice_logo_apos_o_fechamento).

    arvore_de_instrucoes e uma lista onde cada item e:
      - uma tupla de instrucao simples, ex: ("input_int", "X")
      - ou ("if", condicao, then_body, else_body) para blocos if/if-else,
        onde then_body e else_body sao, elas mesmas, arvores (listas).

    No nivel mais externo do programa (_top_level=True) nao existe chave
    de fechamento esperada; um 'block_close' sobrando ali e erro ('}' sem
    'if' correspondente). Em qualquer nivel interno, chegar ao fim das
    linhas SEM um 'block_close' e erro ('if' sem chave de fechamento).
    """
    tree = []
    i = start
    while i < len(lines):
        instr = lines[i]

        if instr[0] == "block_close":
            if _top_level:
                raise SyntaxError("'}' encontrado sem 'if' ou bloco correspondente")
            return tree, i + 1

        if instr[0] == "else_open":
            if _top_level:
                raise SyntaxError("'} else {' encontrado sem 'if' correspondente")
            # Fecha o bloco atual (then_body de um if) sem consumir o
            # token: quem chamou (o tratamento de if_open, um nivel acima)
            # decide o que fazer com o else.
            return tree, i

        if instr[0] == "if_open":
            cond_text = instr[1]
            condition = parse_condition(cond_text)

            then_body, i = parse_block(lines, i + 1, _top_level=False)

            else_body = []
            if i < len(lines) and lines[i][0] == "else_open":
                else_body, i = parse_block(lines, i + 1, _top_level=False)

            tree.append(("if", condition, then_body, else_body))
            continue

        if instr[0] == "while_open":
            cond_text = instr[1]
            condition = parse_condition(cond_text)
            loop_body, i = parse_block(lines, i + 1, _top_level=False)
            tree.append(("while", condition, loop_body))
            continue

        if instr[0] == "for_open":
            (_, var_name, start_token, end_token) = instr
            loop_body, i = parse_block(lines, i + 1, _top_level=False)
            tree.append(("for", var_name, start_token, end_token, loop_body))
            continue

        if instr[0] == "func_open":
            (_, func_name, params) = instr
            if not _top_level:
                raise SyntaxError(
                    f"Declaracao de funcao '{func_name}' so e permitida no nivel "
                    f"mais externo do arquivo (nao dentro de if ou de outra funcao)"
                )
            func_body, i = parse_block(lines, i + 1, _top_level=False)
            tree.append(("func_def", func_name, params, func_body))
            continue

        if instr[0] == "onclick_open":
            (_, key_name) = instr
            if not _top_level:
                raise SyntaxError(
                    f"'OnClick({key_name})' so e permitido no nivel mais externo "
                    f"do arquivo (nao dentro de if, funcao ou outro OnClick)"
                )
            onclick_body, i = parse_block(lines, i + 1, _top_level=False)
            tree.append(("onclick_def", key_name, onclick_body))
            continue

        if instr[0] == "callkey_def_open":
            (_, key_name) = instr
            if not _top_level:
                raise SyntaxError(
                    f"'Funct.CALLKEY.({key_name})' so pode ser declarado no nivel "
                    f"mais externo do arquivo (nao dentro de if, While, funcao, etc)"
                )
            callkey_body, i = parse_block(lines, i + 1, _top_level=False)
            tree.append(("callkey_def", key_name, callkey_body))
            continue

        # instrucao simples (inclui callkey_use, que nao abre bloco)
        tree.append(instr)
        i += 1

    if not _top_level:
        raise SyntaxError("Bloco '{ ... }' nao foi fechado (chave '}' faltando)")

    return tree, i


def tokenize_vic(source: str):
    """Aplica parse_line em cada linha do codigo fonte, descartando vazias/comentarios."""
    tokens = []
    for raw_line in source.splitlines():
        instr = parse_line(raw_line)
        if instr is not None:
            tokens.append(instr)
    return tokens


# ---------------------------------------------------------------------------
# Compilador: junta parser + symbol table + codegen
# ---------------------------------------------------------------------------
def compile_block(tree, symtab, codegen, imported_modules):
    """
    Percorre a arvore de instrucoes (devolvida por parse_block) e gera o
    assembly correspondente, chamando a si mesma para o conteudo de
    blocos if/else (que tambem sao arvores).

    'imported_modules' e um set() compartilhado entre todas as chamadas
    recursivas, contendo os nomes dos modulos ja vistos via [IMPOR] ...;
    """
    for instr in tree:
        kind = instr[0]

        if kind == "input_int":
            (_, var) = instr
            codegen.gen_input_int(var)

        elif kind == "input_str":
            (_, var) = instr
            codegen.gen_input_str(var)

        elif kind == "int_literal":
            (_, var, value) = instr
            codegen.gen_int_literal(var, value)

        elif kind == "str_literal":
            (_, var, text) = instr
            if symtab.exists(var) and symtab.type_of(var) != "str":
                raise TypeError(
                    f"str({var}, \"...\"): '{var}' ja foi declarado como "
                    f"'{symtab.type_of(var)}'; nao pode virar str"
                )
            codegen.gen_str_literal(var, text)

        elif kind == "out":
            (_, var) = instr
            if not symtab.exists(var):
                raise NameError(f"Variavel '{var}' usada em out.{var} antes de ser definida")
            if symtab.type_of(var) == "str":
                codegen.gen_out_str(var)
            else:
                codegen.gen_out_int(var)

        elif kind == "add":
            (_, x, y, dest) = instr
            for v in (x, y):
                if not symtab.exists(v):
                    raise NameError(f"Variavel '{v}' usada em ADD antes de ser definida")
                if symtab.type_of(v) != "int":
                    raise TypeError(f"ADD so funciona com variaveis int; '{v}' e '{symtab.type_of(v)}'")
            codegen.gen_add(x, y, dest)

        elif kind == "sub":
            (_, x, y, dest) = instr
            for v in (x, y):
                if not symtab.exists(v):
                    raise NameError(f"Variavel '{v}' usada em SUB antes de ser definida")
                if symtab.type_of(v) != "int":
                    raise TypeError(f"SUB so funciona com variaveis int; '{v}' e '{symtab.type_of(v)}'")
            codegen.gen_sub(x, y, dest)

        elif kind == "mul":
            (_, x, y, dest) = instr
            for v in (x, y):
                if not symtab.exists(v):
                    raise NameError(f"Variavel '{v}' usada em MUL antes de ser definida")
                if symtab.type_of(v) != "int":
                    raise TypeError(f"MUL so funciona com variaveis int; '{v}' e '{symtab.type_of(v)}'")
            codegen.gen_mul(x, y, dest)

        elif kind == "div":
            (_, x, y, dest) = instr
            for v in (x, y):
                if not symtab.exists(v):
                    raise NameError(f"Variavel '{v}' usada em DIV antes de ser definida")
                if symtab.type_of(v) != "int":
                    raise TypeError(f"DIV so funciona com variaveis int; '{v}' e '{symtab.type_of(v)}'")
            codegen.gen_div(x, y, dest)

        elif kind == "import":
            (_, module_name) = instr
            if module_name not in ("Display", "Teclado", "Bitmap"):
                raise SyntaxError(f"Modulo desconhecido em [IMPOR]: '{module_name}'")
            imported_modules.add(module_name)

        elif kind == "import_file":
            (_, module_name) = instr
            external_modules = compile_block.external_modules
            source_dir = compile_block.source_dir
            vic_path = os.path.join(source_dir, f"{module_name}.vic")
            if not os.path.isfile(vic_path):
                raise FileNotFoundError(
                    f"'[IMPOR] : {module_name}.asm;' espera encontrar '{vic_path}' "
                    f"(mesmo nome, com extensao .vic) para ler as assinaturas das "
                    f"funcoes - arquivo nao encontrado. Lembre-se de tambem montar "
                    f"'{module_name}.asm' junto com este arquivo no MARS (Project ou "
                    f"'Assemble all files in directory')."
                )
            with open(vic_path, "r", encoding="utf-8") as f:
                external_source = f.read()
            external_modules[module_name] = extract_function_signatures(external_source)

        elif kind == "import_call":
            (_, module_name, func_name, args, dest) = instr
            external_modules = compile_block.external_modules
            if module_name not in external_modules:
                raise NameError(
                    f"'IMPORT.{module_name}.{func_name}(...)' usado sem "
                    f"'[IMPOR] : {module_name}.asm;' no topo do arquivo"
                )
            module_signatures = external_modules[module_name]
            if func_name not in module_signatures:
                raise NameError(
                    f"Modulo '{module_name}' (de '{module_name}.vic') nao tem "
                    f"nenhuma funcao chamada '{func_name}'"
                )
            expected_params = module_signatures[func_name]
            if len(args) != len(expected_params):
                raise TypeError(
                    f"Funcao '{module_name}.{func_name}' espera {len(expected_params)} "
                    f"argumento(s) ({', '.join(expected_params)}), mas a chamada passou {len(args)}"
                )
            for arg in args:
                if arg.lstrip("-").isdigit():
                    continue
                if not symtab.exists(arg):
                    raise NameError(f"Variavel '{arg}' usada em IMPORT.{module_name}.{func_name} antes de ser definida")
                if symtab.type_of(arg) != "int":
                    raise TypeError(f"IMPORT.{module_name}.{func_name} so aceita argumentos int; '{arg}' nao e int")
            # a funcao real mora em outro .asm (montado junto no MARS);
            # aqui so geramos a chamada - o 'jal' usa o nome puro da
            # funcao (sem prefixo de modulo), exatamente como o MARS
            # resolve labels entre arquivos montados juntos
            codegen.gen_call(func_name, args, dest)

        elif kind == "display_set":
            (_, display, segment, state) = instr
            if "Display" not in imported_modules:
                raise NameError(
                    f"'{display}.set(...)' usado sem '[IMPOR] Display;' no topo do arquivo"
                )
            codegen.gen_display_set(display, segment, state)

        elif kind == "display_show_digit":
            (_, display, value_token) = instr
            if "Display" not in imported_modules:
                raise NameError(
                    f"'{display}.show_digit(...)' usado sem '[IMPOR] Display;' no topo do arquivo"
                )
            codegen.gen_display_show_digit(display, value_token)

        elif kind == "display_clear":
            (_, display) = instr
            if "Display" not in imported_modules:
                raise NameError(
                    f"'{display}.clear()' usado sem '[IMPOR] Display;' no topo do arquivo"
                )
            codegen.gen_display_clear(display)

        elif kind == "bitmap_set_pixel":
            (_, x_tok, y_tok, cor_tok) = instr
            if "Bitmap" not in imported_modules:
                raise NameError("'Bitmap.set_pixel(...)' usado sem '[IMPOR] Bitmap;' no topo do arquivo")
            codegen.gen_bitmap_set_pixel(x_tok, y_tok, cor_tok)

        elif kind == "bitmap_fill_screen":
            (_, cor_tok) = instr
            if "Bitmap" not in imported_modules:
                raise NameError("'Bitmap.fill_screen(...)' usado sem '[IMPOR] Bitmap;' no topo do arquivo")
            codegen.gen_bitmap_fill_screen(cor_tok)

        elif kind == "bitmap_draw_square":
            (_, x_tok, y_tok, size_tok, cor_tok) = instr
            if "Bitmap" not in imported_modules:
                raise NameError("'Bitmap.draw_square(...)' usado sem '[IMPOR] Bitmap;' no topo do arquivo")
            codegen.gen_bitmap_draw_square(x_tok, y_tok, size_tok, cor_tok)

        elif kind == "bitmap_clear_screen":
            if "Bitmap" not in imported_modules:
                raise NameError("'Bitmap.clear_screen()' usado sem '[IMPOR] Bitmap;' no topo do arquivo")
            codegen.gen_bitmap_clear_screen()

        elif kind == "return":
            (_, value_token) = instr
            codegen.gen_return(value_token)

        elif kind == "call":
            (_, func_name, args, dest) = instr
            known_functions = compile_block.known_functions
            if func_name not in known_functions:
                raise NameError(
                    f"Funcao '{func_name}' chamada em CALL antes de ser declarada "
                    f"(declare a funcao com 'funct.(...).{func_name}{{ ... }}' antes de usa-la)"
                )
            expected_params = known_functions[func_name]
            if len(args) != len(expected_params):
                raise TypeError(
                    f"Funcao '{func_name}' espera {len(expected_params)} argumento(s) "
                    f"({', '.join(expected_params)}), mas CALL passou {len(args)}"
                )
            for arg in args:
                if arg.lstrip("-").isdigit():
                    continue
                if not symtab.exists(arg):
                    raise NameError(f"Variavel '{arg}' usada em CALL antes de ser definida")
                if symtab.type_of(arg) != "int":
                    raise TypeError(f"CALL so aceita argumentos int; '{arg}' nao e int")
            codegen.gen_call(func_name, args, dest)

        elif kind == "onclick_def":
            (_, key_name, onclick_body) = instr
            if "Teclado" not in imported_modules:
                raise NameError(
                    f"'OnClick({key_name})' usado sem '[IMPOR] Teclado;' no topo do arquivo"
                )

            callback_label = codegen._new_label(f"onclick_{key_name}_callback")

            callback_text, callback_data_decl, cb_mul, cb_div, cb_display = compile_onclick_callback(
                callback_label, onclick_body, imported_modules
            )
            codegen._onclick_callbacks.append(callback_text)
            codegen._onclick_data_decls.append(callback_data_decl)
            codegen._uses_mul = codegen._uses_mul or cb_mul
            codegen._uses_div = codegen._uses_div or cb_div
            codegen._uses_display = codegen._uses_display or cb_display

            codegen.gen_onclick_call(key_name, callback_label)

        elif kind == "callkey_def":
            (_, key_name, callkey_body) = instr
            if "Teclado" not in imported_modules:
                raise NameError(
                    f"'Funct.CALLKEY.({key_name})' usado sem '[IMPOR] Teclado;' no topo do arquivo"
                )
            if key_name in codegen._callkey_labels:
                raise SyntaxError(
                    f"'Funct.CALLKEY.({key_name})' declarado mais de uma vez"
                )

            body_label = f"callkey_{key_name}_body"

            # compila o corpo como mini-funcao sem parametros e sem retorno,
            # igual ao OnClick, usando compile_onclick_callback
            body_text, body_data_decl, cb_mul, cb_div, cb_display = compile_onclick_callback(
                body_label, callkey_body, imported_modules
            )
            codegen._onclick_callbacks.append(body_text)
            codegen._onclick_data_decls.append(body_data_decl)
            codegen._uses_mul = codegen._uses_mul or cb_mul
            codegen._uses_div = codegen._uses_div or cb_div
            codegen._uses_display = codegen._uses_display or cb_display
            codegen._uses_keyboard = True

            # registra o label para que CALLKEY.(N) saiba onde pular
            codegen._callkey_labels[key_name] = body_label
            # callkey_def NAO gera codigo inline no fluxo principal --
            # a declaracao fica so como sub-rotina, invocada por CALLKEY.(N)

        elif kind == "callkey_use":
            (_, key_name) = instr
            if "Teclado" not in imported_modules:
                raise NameError(
                    f"'CALLKEY.({key_name})' usado sem '[IMPOR] Teclado;' no topo do arquivo"
                )
            codegen.gen_callkey_call(key_name)

        elif kind == "if":
            (_, condition, then_body, else_body) = instr
            has_else = len(else_body) > 0

            label_else_or_end, label_end = codegen.gen_if_else_skeleton(condition, has_else)

            compile_block(then_body, symtab, codegen, imported_modules)

            if has_else:
                codegen.emit(f"\tj {label_end}")
                codegen.emit(f"{label_else_or_end}:")
                compile_block(else_body, symtab, codegen, imported_modules)

            codegen.emit(f"{label_end}:")

        elif kind == "while":
            (_, condition, loop_body) = instr

            label_teste, label_fim = codegen.gen_while_skeleton(condition)

            compile_block(loop_body, symtab, codegen, imported_modules)

            codegen.emit(f"\tj {label_teste}")
            codegen.emit(f"{label_fim}:")

        elif kind == "for":
            (_, var_name, start_token, end_token, loop_body) = instr

            for token in (start_token, end_token):
                if token.lstrip("-").isdigit():
                    continue
                if not symtab.exists(token):
                    raise NameError(f"Variavel '{token}' usada em For antes de ser definida")
                if symtab.type_of(token) != "int":
                    raise TypeError(f"For so aceita limites int; '{token}' nao e int")

            var_name, label_teste, label_fim = codegen.gen_for_skeleton(
                var_name, start_token, end_token
            )

            compile_block(loop_body, symtab, codegen, imported_modules)

            codegen.gen_for_increment_and_jump(var_name, label_teste)
            codegen.emit(f"{label_fim}:")

        elif kind == "array_declare":
            (_, array_name, size) = instr
            if symtab.exists(array_name):
                raise TypeError(f"Variavel '{array_name}' ja foi declarada como '{symtab.type_of(array_name)}'")
            symtab.declare_array(array_name, size)

        elif kind == "array_set":
            (_, array_name, index_token, value_token) = instr
            if not symtab.exists(array_name) or symtab.type_of(array_name) != "array":
                raise NameError(f"'{array_name}' nao e um array declarado (use int[]({array_name}, tamanho) antes)")
            for token in (index_token, value_token):
                if token.lstrip("-").isdigit():
                    continue
                if not symtab.exists(token):
                    raise NameError(f"Variavel '{token}' usada em SET antes de ser definida")
                if symtab.type_of(token) != "int":
                    raise TypeError(f"SET so aceita indice/valor int; '{token}' nao e int")
            codegen.gen_array_set(array_name, index_token, value_token)

        elif kind == "array_get":
            (_, array_name, index_token, dest) = instr
            if not symtab.exists(array_name) or symtab.type_of(array_name) != "array":
                raise NameError(f"'{array_name}' nao e um array declarado (use int[]({array_name}, tamanho) antes)")
            if not index_token.lstrip("-").isdigit():
                if not symtab.exists(index_token):
                    raise NameError(f"Variavel '{index_token}' usada em GET antes de ser definida")
                if symtab.type_of(index_token) != "int":
                    raise TypeError(f"GET so aceita indice int; '{index_token}' nao e int")
            codegen.gen_array_get(array_name, index_token, dest)

        elif kind == "readfile":
            (_, filename, buffer_var, dest_count) = instr
            # buffer_var vai ser declarado como str (ou deve ja ser str)
            if symtab.exists(buffer_var) and symtab.type_of(buffer_var) != "str":
                raise TypeError(
                    f"READFILE: '{buffer_var}' ja existe como '{symtab.type_of(buffer_var)}'; "
                    f"o buffer de leitura deve ser uma variavel str"
                )
            # garante que buffer_var e str (cria se nao existir)
            symtab.resolve_str(buffer_var)
            codegen.gen_readfile(filename, buffer_var, dest_count)

        elif kind == "writefile":
            (_, filename, string_var) = instr
            if not symtab.exists(string_var) or symtab.type_of(string_var) != "str":
                raise TypeError(
                    f"WRITEFILE: '{string_var}' nao e uma variavel str; "
                    f"declare com input.str({string_var}) ou READFILE(..., {string_var})"
                )
            codegen.gen_writefile(filename, string_var)

        else:
            raise SyntaxError(f"Instrucao nao implementada: {kind}")


def compile_onclick_callback(callback_label, body, imported_modules):
    """
    Compila o corpo de um 'OnClick(N) { ... }' como uma mini-funcao SEM
    parametros e SEM valor de retorno (chamada via 'jalr', nao 'jal' com
    $v0 esperado depois). Memoria isolada, igual uma funcao normal.

    'return' nao e suportado dentro de OnClick (nao ha sentido em devolver
    um valor de um callback de clique); se aparecer, da erro claro.
    """
    cb_symtab = SymbolTable()
    cb_codegen = CodeGen(cb_symtab)

    # ao deixar _function_exit_label em None, qualquer 'return' dentro do
    # corpo do OnClick gera o mesmo erro de "'return' usado fora de uma
    # declaracao de funcao" - reaproveitando a checagem que ja existe em
    # gen_return, sem precisar duplicar logica
    cb_codegen.emit(f"{callback_label}:")
    cb_codegen.emit("\taddi $sp, $sp, -4")
    cb_codegen.emit("\tsw $ra, 0($sp)")

    compile_block(body, cb_symtab, cb_codegen, imported_modules)

    cb_codegen.emit("\tlw $ra, 0($sp)")
    cb_codegen.emit("\taddi $sp, $sp, 4")
    cb_codegen.emit("\tjr $ra")

    cb_text = "\n".join(cb_codegen.lines)
    cb_data_label = f"data_{callback_label}"
    cb_text = re.sub(r"\bdata\b", cb_data_label, cb_text)

    int_words = max(cb_symtab.int_count(), 1)
    data_decl = f"{cb_data_label}: .space {int_words * 4}"

    return cb_text, data_decl, cb_codegen._uses_mul, cb_codegen._uses_div, cb_codegen._uses_display


def compile_function(func_name, params, body, known_functions, imported_modules):
    """
    Compila uma declaracao de funcao ('funct.(a,b).hello{ ... }') de forma
    ISOLADA: cria uma SymbolTable e um CodeGen proprios (memoria separada
    do programa principal e de outras funcoes), executa o prologo
    (salva $ra e os parametros recebidos via $a0, $a1, ...), compila o
    corpo (que pode usar qualquer instrucao do .vic, inclusive if/ADD/etc,
    e tambem 'return'), e fecha com o epilogo (restaura $ra, jr $ra).

    Devolve o texto assembly da funcao, pronto para ser anexado ao final
    do .asm (fora do fluxo do main, alcancavel so via 'jal').

    'known_functions' e o mesmo dict compartilhado usado para resolver
    CALL (nome_funcao -> lista de parametros), permitindo que uma funcao
    chame outra (desde que a outra ja tenha sido declarada antes, igual
    exigido para chamadas no programa principal).
    """
    func_symtab = SymbolTable()
    func_codegen = CodeGen(func_symtab)

    exit_label = f"fim_{func_name}"
    func_codegen._function_exit_label = exit_label

    # prologo: reserva espaco na memoria local da funcao para cada
    # parametro, e copia os argumentos recebidos ($a0, $a1, ...) para la.
    arg_registers = ["$a0", "$a1", "$a2", "$a3"]
    if len(params) > len(arg_registers):
        raise SyntaxError(
            f"Funcao '{func_name}' tem {len(params)} parametros, mas o maximo "
            f"suportado e {len(arg_registers)} (limite de registradores $a0-$a3)"
        )

    func_codegen.emit(f"{func_name}:")
    func_codegen.emit("\taddi $sp, $sp, -4")
    func_codegen.emit("\tsw $ra, 0($sp)")
    for param, reg in zip(params, arg_registers):
        off = func_symtab.resolve_int(param)
        func_codegen.emit(f"\tla $s0, data")
        func_codegen.emit(f"\tsw {reg}, {off}($s0)")

    # corpo da funcao: compilado com compile_block, igual ao corpo de um if -
    # entao 'return', if/else, ADD, Display, etc. tudo funciona dentro
    compile_block(body, func_symtab, func_codegen, imported_modules)

    # epilogo
    func_codegen.emit(f"{exit_label}:")
    func_codegen.emit("\tlw $ra, 0($sp)")
    func_codegen.emit("\taddi $sp, $sp, 4")
    func_codegen.emit("\tjr $ra")

    func_text = "\n".join(func_codegen.lines)
    # renomeia o label 'data' (generico, usado internamente pelo CodeGen)
    # para um label unico desta funcao, evitando colisao com o 'data'
    # do programa principal ou de outras funcoes
    func_data_label = f"data_{func_name}"
    func_text = re.sub(r"\bdata\b", func_data_label, func_text)

    int_words = max(func_symtab.int_count(), 1)
    data_decl = f"{func_data_label}: .space {int_words * 4}"

    return func_text, data_decl, func_codegen._uses_mul, func_codegen._uses_div, func_codegen._uses_display


def extract_function_signatures(vic_source: str) -> dict:
    """
    Le um codigo .vic (de outro arquivo, que sera montado separadamente
    no MARS) e devolve so as ASSINATURAS das funcoes declaradas nele -
    nome -> lista de parametros - sem gerar nenhum assembly. Usado para
    resolver '[IMPOR] : Modulo.asm;' + 'IMPORT.Modulo.funcao(...).dest':
    o compilador precisa saber quantos parametros 'funcao' espera para
    gerar a chamada certa, mas o CODIGO da funcao mora no Modulo.asm
    separado (que sera montado junto no MARS via Project ou pasta).
    """
    tokens = tokenize_vic(vic_source)
    tree, _ = parse_block(tokens)

    signatures = {}
    for instr in tree:
        if instr[0] == "func_def":
            (_, func_name, params, _func_body) = instr
            signatures[func_name] = params
    return signatures


def compile_vic(source: str, source_dir: str = ".") -> str:
    symtab = SymbolTable()
    codegen = CodeGen(symtab)
    imported_modules = set()

    tokens = tokenize_vic(source)
    tree, _ = parse_block(tokens)

    # known_functions e populado INCREMENTALMENTE, na mesma ordem em que
    # as declaracoes 'funct.(...).nome{...}' aparecem no arquivo. Isso e
    # o que garante que uma funcao (ou o main_body) so pode chamar (CALL)
    # uma funcao que ja foi declarada ANTES dela no texto - chamar uma
    # funcao declarada mais abaixo no arquivo e erro de compilacao.
    known_functions = {}
    compile_block.known_functions = known_functions

    # external_modules: nome_do_modulo -> {nome_funcao: [parametros]},
    # populado por 'import_file' a medida que [IMPOR] : Arquivo.asm; e
    # processado, na ordem em que aparece no arquivo (mesma regra de
    # "declarar antes de usar" aplicada a funcoes locais)
    compile_block.external_modules = {}
    compile_block.source_dir = source_dir

    compiled_functions = []
    extra_data_decls = []
    main_body = []

    for instr in tree:
        if instr[0] == "func_def":
            (_, func_name, params, func_body) = instr
            if func_name in known_functions:
                raise SyntaxError(f"Funcao '{func_name}' declarada mais de uma vez")

            # compila a funcao AGORA, com known_functions contendo so as
            # funcoes declaradas ate este ponto (nao as que vem depois)
            func_text, data_decl, uses_mul, uses_div, uses_display = compile_function(
                func_name, params, func_body, known_functions, imported_modules
            )

            # SO DEPOIS de compilar com sucesso, registra a funcao como
            # conhecida - assim ela fica disponivel para CALLs seguintes
            # (no main_body ou em funcoes declaradas mais abaixo), mas
            # nao para o proprio corpo dela (sem recursao direta, a nao
            # ser que voce queira liberar isso depois)
            known_functions[func_name] = params

            compiled_functions.append(func_text)
            extra_data_decls.append(data_decl)
            codegen._uses_mul = codegen._uses_mul or uses_mul
            codegen._uses_div = codegen._uses_div or uses_div
            codegen._uses_display = codegen._uses_display or uses_display
        else:
            # instrucao do programa principal: compila imediatamente,
            # na ordem em que aparece, com known_functions contendo
            # exatamente as funcoes declaradas ate aqui
            compile_block([instr], symtab, codegen, imported_modules)
            main_body.append(instr)

    return codegen.build(
        extra_functions_asm=compiled_functions,
        extra_data_decls=extra_data_decls,
    )


# ---------------------------------------------------------------------------
# Interface de linha de comando
#
# Uso:
#   python3 VIC_interpreter.py meuprograma.vic
#       -> gera meuprograma.asm no mesmo diretorio
#
#   python3 VIC_interpreter.py meuprograma.vic -o outro_nome.asm
#       -> gera outro_nome.asm
#
#   python3 VIC_interpreter.py
#       -> sem argumentos, roda o exemplo embutido abaixo (modo demo)
# ---------------------------------------------------------------------------
def _main():
    import sys

    args = sys.argv[1:]

    if not args:
        # Modo demo: nenhum arquivo .vic passado, usa o exemplo embutido
        exemplo = """
        input.int(X)
        input.int(Y)
        ADD(X,Y).X
        out.X
        """
        print("(modo demo - nenhum arquivo .vic informado, usando exemplo embutido)\n")
        print(compile_vic(exemplo))
        return

    input_path = args[0]

    if len(args) >= 3 and args[1] == "-o":
        output_path = args[2]
    else:
        # troca a extensao .vic por .asm automaticamente
        if input_path.endswith(".vic"):
            output_path = input_path[: -len(".vic")] + ".asm"
        else:
            output_path = input_path + ".asm"

    with open(input_path, "r", encoding="utf-8") as f:
        source = f.read()

    source_dir = os.path.dirname(os.path.abspath(input_path))

    try:
        asm = compile_vic(source, source_dir=source_dir)
    except (SyntaxError, NameError, TypeError, FileNotFoundError) as e:
        print(f"Erro ao compilar {input_path}: {e}")
        sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(asm)

    print(f"OK: {input_path} -> {output_path}")


if __name__ == "__main__":
    _main()