# VIC — Victor Interpreter Compiler

Manual de referência completo da linguagem `.vic`.

O compilador lê um arquivo `.vic` e gera um arquivo `.asm` compatível com o
simulador MARS/MIPS. Para compilar:

```bash
python3 VIC_interpreter.py meuprograma.vic
# gera meuprograma.asm automaticamente
```

---

## Índice

1. [Tipos de dados](#1-tipos-de-dados)
2. [Entrada e saída](#2-entrada-e-saída)
3. [Operações aritméticas](#3-operações-aritméticas)
4. [Condicionais](#4-condicionais)
5. [Laços](#5-laços)
6. [Arrays](#6-arrays)
7. [Funções](#7-funções)
8. [Módulos de hardware](#8-módulos-de-hardware)
9. [Arquivos](#9-arquivos)
10. [Módulos externos](#10-módulos-externos)
11. [Erros de runtime](#11-erros-de-runtime)
12. [Regras gerais da linguagem](#12-regras-gerais-da-linguagem)
13. [Exemplo completo](#13-exemplo-completo)

---

## 1. Tipos de dados

O `.VIC` tem três tipos: **int**, **str** e **array**.

### Inteiro (int)

```vic
int(X, 5)        # X = 5 (valor literal direto)
input.int(X)     # X = valor digitado pelo usuário no MARS
```

- Cada variável int ocupa 4 bytes (uma word MIPS).
- Uma mesma variável pode ser sobrescrita quantas vezes quiser com `int(X, N)` ou `input.int(X)`.
- **Não pode** ter o mesmo nome que uma variável `str` ou `array` já declarada.

### String (str)

```vic
input.str(H)     # H = string digitada pelo usuário (até 1024 caracteres)
```

- Cada variável str reserva **1024 bytes** de buffer.
- O `\n` do final do input é removido automaticamente (o compilador gera
  o código de limpeza).
- Strings não suportam operações aritméticas, mas podem ser comparadas
  em condicionais com `==` e `!=`.
- **Não pode** ter o mesmo nome que um `int` ou `array` já declarado.

### Array de inteiros

```vic
int[](vetor, 100)   # declara vetor com 100 inteiros (inicializados a 0)
```

- Cada elemento ocupa 4 bytes (`vetor[i]` está em `base + i * 4`).
- O tamanho é fixo na hora da declaração e não pode ser alterado.
- Acesso por `SET` e `GET` (ver seção [Arrays](#6-arrays)).

---

## 2. Entrada e saída

### Imprimir valor

```vic
out.X            # imprime o valor de X (int ou str, detectado automaticamente)
```

- Se `X` for `int`, usa syscall 1 (print integer).
- Se `X` for `str`, usa syscall 4 (print string).

### Ler inteiro do teclado

```vic
input.int(X)     # lê um inteiro digitado no console do MARS e guarda em X
```

### Ler string do teclado

```vic
input.str(H)     # lê uma linha de texto (até 1023 chars) e guarda em H
```

---

## 3. Operações aritméticas

Todas as operações seguem o padrão `OP(A, B).DEST` — calcula `A OP B` e
guarda o resultado em `DEST`. `DEST` pode ser uma variável nova ou qualquer
das existentes (incluindo os próprios `A` ou `B`).

### Soma

```vic
ADD(X, Y).Z      # Z = X + Y
ADD(X, Y).X      # X = X + Y  (acumula em X)
```

### Subtração

```vic
SUB(X, Y).Z      # Z = X - Y  (X menos Y)
```

### Multiplicação segura

```vic
MUL(X, Y).Z      # Z = X * Y  (com detecção de overflow)
```

- Se o resultado não couber em 32 bits com sinal, o programa **imprime
  uma mensagem de erro e termina** em vez de prosseguir com valor inválido.

### Divisão segura

```vic
DIV(X, Y).Z      # Z = X / Y  (quociente inteiro, com detecção de erros)
```

- Se `Y == 0`: imprime `"Erro: divisao por zero"` e termina.
- Se ocorrer overflow (dividendo = `INT_MIN`, divisor = `-1`): imprime
  `"Erro: overflow na divisao"` e termina.

> **Nota:** `MUL` e `DIV` incluem automaticamente as rotinas `mul_segura` e
> `div_segura` no `.asm` gerado, além de um handler de exceções de hardware
> (`.ktext 0x80000180`) como segunda camada de proteção.

---

## 4. Condicionais

### if simples

```vic
if (X == Y) {
    out.X
}
```

### if / else

```vic
if (X < Y) {
    out.X
} else {
    out.Y
}
```

### Operadores relacionais disponíveis

| Operador | Significado        | Funciona com int | Funciona com str |
|----------|--------------------|:---:|:---:|
| `==`     | igual              | ✓   | ✓   |
| `!=`     | diferente          | ✓   | ✓   |
| `<`      | menor que          | ✓   | —   |
| `>`      | maior que          | ✓   | —   |
| `<=`     | menor ou igual     | ✓   | —   |
| `>=`     | maior ou igual     | ✓   | —   |

### Condição composta com AND

```vic
if (X < Y && A == B) {
    # só entra se AMBAS as condições forem verdadeiras
}
```

### Condição composta com OR

```vic
if (X < Y || A == B) {
    # entra se QUALQUER das condições for verdadeira
}
```

> **Limitação:** não é permitido misturar `&&` e `||` na mesma condição
> (`X < Y && A == B || C`). Use `if`s aninhados nesses casos.

### Comparação de string

```vic
input.str(H)
if (H == "QAT 01") {
    out.H
}
if (H != "sair") {
    # continua
}
```

- O interpretador gera automaticamente a rotina `strcmp` no `.asm`.
- Comparações `<`, `>`, `<=`, `>=` entre strings dão **erro de compilação**.
- Comparar `str` com `int` na mesma condição também dá **erro de compilação**.

---

## 5. Laços

### While

Executa o bloco enquanto a condição for verdadeira. Reavalia antes de cada iteração.

```vic
int(X, 0)
int(UM, 1)
While(X < 10) {
    out.X
    ADD(X, UM).X
}
```

- Aceita todos os operadores e combinações `&&`/`||` da seção de condicionais.
- Funciona com comparação de string também:
  ```vic
  input.str(H)
  While(H != "sair") {
      out.H
      input.str(H)
  }
  ```

### For

Loop com variável de controle, valor inicial, limite final (inclusivo) e
incremento automático de 1.

```vic
For(i, 0, 10) {
    out.i
    # executa com i = 0, 1, 2, ..., 10  (11 iterações)
}
```

- `i` é criado como `int` automaticamente se não existir.
- Os limites `inicio` e `fim` podem ser variáveis int ou literais numéricos:
  ```vic
  int(inicio, 2)
  int(fim, 8)
  For(j, inicio, fim) {
      out.j
  }
  ```
- `For`s podem ser aninhados (cada um usa sua própria variável de controle):
  ```vic
  For(i, 0, 2) {
      For(j, 0, 2) {
          out.i
          out.j
      }
  }
  ```

> O `For` incrementa de 1 em 1 (sempre positivo). Para decrementar ou
> incrementar de outro passo, use `While` com `ADD`/`SUB` manuais.

---

## 6. Arrays

### Declaração

```vic
int[](vetor, 100)   # 100 inteiros, todos iniciados a 0
```

### Escrever num elemento

```vic
SET(vetor, 5, 42)     # vetor[5] = 42  (índice literal)
SET(vetor, i, valor)  # vetor[i] = valor  (índice e valor podem ser variáveis)
```

- O índice e o valor podem ser variáveis `int` ou literais numéricos.

### Ler um elemento

```vic
GET(vetor, 5).X       # X = vetor[5]
GET(vetor, i).X       # X = vetor[i]
```

### Exemplo: preencher e somar array com For

```vic
int[](nums, 5)

For(i, 0, 4) {
    SET(nums, i, i)       # nums = [0, 1, 2, 3, 4]
}

int(soma, 0)
For(i, 0, 4) {
    GET(nums, i).valor
    ADD(soma, valor).soma
}
out.soma                  # imprime 10
```

> **Sem verificação de limites em runtime.** Acessar um índice fora dos
> limites declarados pode corromper memória. O compilador não gera checagem
> automática de bounds.

---

## 7. Funções

### Declaração

```vic
funct.(a,b).hello{
    int(R, 0)
    ADD(a,b).R
    return R
}
```

- Sintaxe: `funct.(param1, param2, ...).nome_funcao{`
- O corpo pode conter qualquer instrução `.vic` (`if`, `While`, `For`,
  `ADD`, `Display`, `OnClick`, etc.).
- `return valor` termina a função e devolve o valor em `$v0`.
  - `valor` pode ser uma variável `int` ou um literal numérico.
- Máximo de **4 parâmetros** (limitação dos registradores `$a0`–`$a3`).
- Parâmetros são sempre `int`.
- Cada função tem seu **próprio espaço de memória** isolado — variáveis
  locais e parâmetros não colidem com o programa principal nem com outras
  funções, mesmo que tenham o mesmo nome.

### Chamada

```vic
int(X, 3)
int(Y, 5)
CALL(hello, X, Y).Z    # Z = hello(X, Y) = 8
out.Z
```

- Sintaxe: `CALL(nome_funcao, arg1, arg2, ...).destino`
- `destino` recebe o valor de retorno (tipo `int`).
- Argumentos podem ser variáveis `int` ou literais numéricos.
- A função deve ter sido **declarada antes** de ser chamada no arquivo.
- Uma função pode chamar outra declarada antes dela:
  ```vic
  funct.(a,b).somar{
      int(R, 0)
      ADD(a,b).R
      return R
  }

  funct.(a,b,c).somar_tres{
      int(parcial, 0)
      CALL(somar, a, b).parcial
      int(R, 0)
      ADD(parcial, c).R
      return R
  }

  CALL(somar_tres, 1, 2, 3).resultado
  out.resultado    # 6
  ```

> **Recursão direta não é suportada** — uma função não pode chamar a
> si mesma.

---

## 8. Módulos de hardware

Ativados por `[IMPOR]` no topo do arquivo.

### Módulo Display (7 segmentos do Digital Lab Sim)

```vic
[IMPOR] Display;
```

Disponibiliza dois displays:
- `Display1` → display **esquerdo** (`0xFFFF0011`)
- `Display2` → display **direito** (`0xFFFF0010`)

#### Ligar/desligar um segmento individual

```vic
Display1.set(a, on)     # liga o segmento 'a' do display esquerdo
Display1.set(b, off)    # desliga o segmento 'b'
Display2.set(g, on)     # liga o segmento 'g' do display direito
```

Segmentos disponíveis: `a`, `b`, `c`, `d`, `e`, `f`, `g`, `p` (ponto decimal).

#### Mostrar um dígito completo

```vic
Display1.show_digit(7)   # mostra o dígito 7 no display esquerdo (literal)
Display2.show_digit(X)   # mostra o valor da variável X no display direito
```

- Aceita dígitos 0–9 e letras A–F (16 valores, display hexadecimal).
- Argumento pode ser variável `int` ou literal numérico.

#### Apagar o display

```vic
Display1.clear()    # apaga todos os segmentos do display esquerdo
Display2.clear()    # apaga todos os segmentos do display direito
```

---

### Módulo Teclado (Digital Lab Sim)

```vic
[IMPOR] Teclado;
```

Disponibiliza a instrução `OnClick` e inclui automaticamente as rotinas
`scan_key`, `decode_key` e `wait_for_key` no `.asm` gerado.

#### OnClick — aguardar tecla e executar bloco

```vic
OnClick(1) {
    Display1.show_digit(1)
}

OnClick(A) {
    Display1.clear()
}
```

- **Comportamento bloqueante**: o programa para nesse ponto até a tecla
  ser pressionada, executa o bloco, depois continua para o próximo `OnClick`.
- **Teclas disponíveis:** `0`–`9` e `A`–`F` (teclado hexadecimal do Digital Lab Sim).
- O compilador converte o nome da tecla para o código bruto automaticamente:

| Tecla | Código | Tecla | Código |
|-------|--------|-------|--------|
| `0`   | 0x81   | `A`   | 0x18   |
| `1`   | 0x11   | `B`   | 0x28   |
| `2`   | 0x12   | `C`   | 0x48   |
| `3`   | 0x14   | `D`   | 0x88   |
| `4`   | 0x21   | `E`   | 0x84   |
| `5`   | 0x22   | `F`   | 0x82   |
| `6`   | 0x24   |       |        |
| `7`   | 0x41   |       |        |
| `8`   | 0x42   |       |        |
| `9`   | 0x44   |       |        |

- O corpo do `OnClick` aceita qualquer instrução `.vic`.
- `OnClick` só pode aparecer no nível mais externo do arquivo (não dentro
  de `if`, funções ou outro `OnClick`).

---

## 9. Arquivos

Leitura e escrita de arquivos `.txt` via syscalls do MARS. **Não requer `[IMPOR]`.**

### Ler arquivo

```vic
READFILE("numeros.txt", conteudo).N
```

- Lê o arquivo `"numeros.txt"` para a variável `str` chamada `conteudo`.
- Guarda o número de bytes lidos na variável `int` `N`.
- Se `N == -1`, ocorreu um erro (arquivo não encontrado, sem permissão, etc.).
- `conteudo` é criado automaticamente como `str` se não existir ainda.
- Buffer de 1024 bytes (igual a qualquer outra variável `str`).

### Escrever arquivo

```vic
WRITEFILE("saida.txt", conteudo)
```

- Escreve o conteúdo da variável `str` `conteudo` no arquivo `"saida.txt"`.
- **Sobrescreve** o arquivo se já existir; cria se não existir.
- O tamanho é calculado automaticamente (via `strlen`).
- `conteudo` deve ser uma variável `str` já declarada.

### Exemplo completo: ler e copiar

```vic
READFILE("original.txt", dados).N
if (N != -1) {
    WRITEFILE("copia.txt", dados)
}
```

> **Limitação:** o MARS não suporta `seek`, então não é possível fazer
> append diretamente. Para anexar conteúdo, a solução manual é ler o
> arquivo existente, concatenar com o novo conteúdo e reescrever tudo.

---

## 10. Módulos externos

Permite que um arquivo `.vic` chame funções definidas noutro arquivo `.vic`
compilado separadamente. Os dois `.asm` gerados são montados juntos no MARS
(via **Project** ou **Assemble all files in directory**).

### Declarar o módulo externo

```vic
[IMPOR] : Soma.asm;
```

- O compilador lê `Soma.vic` (mesmo nome, extensão trocada) no mesmo
  diretório para **descobrir as assinaturas** das funções (nomes e
  parâmetros).
- O código das funções **não é copiado** — apenas o `jal` correto é gerado.
- `Soma.asm` precisa existir em tempo de montagem no MARS.

### Chamar função do módulo externo

```vic
IMPORT.Soma.soma(X, Y).H
```

- Sintaxe: `IMPORT.NomeModulo.nomeFuncao(arg1, arg2, ...).destino`
- Equivale a um `jal soma` no assembly, resolvido pelo linker do MARS.
- Argumentos podem ser variáveis `int` ou literais numéricos.
- Validações em tempo de compilação: o módulo foi importado, a função
  existe, o número de argumentos está correto, os tipos são `int`.

### Arquivos biblioteca (sem `main`)

Um arquivo `.vic` que contém **apenas** declarações de função (sem código
solto fora de funções) gera um `.asm` sem `main:` — só o código das funções
com `.globl`. Isso evita o erro de "símbolo redefinido" no MARS.

```
# Soma.vic — arquivo biblioteca puro
funct.(a,b).soma{
    int(R, 0)
    ADD(a,b).R
    return R
}
# sem código solto -> gera Soma.asm sem main:
```

```
# main.vic — programa principal
[IMPOR] : Soma.asm;

int(X, 3)
int(Y, 5)
IMPORT.Soma.soma(X,Y).H
out.H
```

---

## 11. Erros de runtime

Alguns erros são detectados **em tempo de compilação** (geram mensagem de
erro do compilador Python e não produzem `.asm`). Outros são detectados
**em tempo de execução** dentro do MARS.

### Erros de compilação (exemplos)

| Situação | Mensagem |
|----------|----------|
| Variável usada antes de ser definida | `Variavel 'X' usada em out.X antes de ser definida` |
| Tipo errado em operação | `ADD so funciona com variaveis int; 'H' e 'str'` |
| Chave `{` não fechada | `Bloco '{ ... }' nao foi fechado (chave '}' faltando)` |
| Função chamada antes de declarada | `Funcao 'soma' chamada em CALL antes de ser declarada` |
| Número errado de argumentos | `Funcao 'soma' espera 2 argumento(s) (a, b), mas CALL passou 3` |
| `OnClick` sem `[IMPOR] Teclado;` | `'OnClick(1)' usado sem '[IMPOR] Teclado;' no topo do arquivo` |
| Array não declarado | `'vetor' nao e um array declarado (use int[](vetor, tamanho) antes)` |
| Modulo externo não importado | `'IMPORT.Soma.soma(...)' usado sem '[IMPOR] : Soma.asm;' no topo do arquivo` |
| Comparar string com `<` | `Comparacao de string so suporta == e !=; '<' nao e valido para comparar strings` |

### Erros de runtime no MARS (gerados pelo `.asm`)

| Situação | Mensagem no console do MARS |
|----------|-----------------------------|
| Overflow em `MUL` | `Erro: overflow na multiplicacao` |
| Divisão por zero em `DIV` | `Erro: divisao por zero` |
| Overflow em `DIV` | `Erro: overflow na divisao` |
| Exceção de hardware (overflow de `add`) | `Erro: excecao aritmetica de hardware (overflow/div por zero)` |

---

## 12. Regras gerais da linguagem

- **Uma instrução por linha.** Não há separador `;` entre instruções (só
  no `[IMPOR]`).
- **Comentários** com `#` no início da linha:
  ```vic
  # isso é um comentário e será ignorado pelo compilador
  ```
- **Nomes de variáveis** podem ter letras, dígitos e `_`, mas devem começar
  com letra ou `_`. Exemplos válidos: `X`, `soma`, `valor_1`, `_tmp`.
- **Sensível a maiúsculas/minúsculas:** `x` e `X` são variáveis diferentes.
  `ADD`, `SUB`, `MUL`, `DIV`, `SET`, `GET`, `CALL`, `IMPORT`, `READFILE`,
  `WRITEFILE`, `For`, `While`, `OnClick` devem ser escritos **exatamente**
  como mostrado.
- **Blocos** com `{` e `}` em linhas próprias (sem código na mesma linha que
  a chave de abertura, exceto em `if (cond) {` e `funct.(...)nome{`).
- **`[IMPOR]`** deve aparecer antes de qualquer uso do módulo.
- **Declaração antes de uso**: variáveis devem ser declaradas (com `int(X,N)`,
  `input.int(X)`, `input.str(H)`, `int[](v, N)`) antes de serem usadas em
  operações. Funções devem ser declaradas antes de serem chamadas.

---

## 13. Exemplo completo

Programa que lê dois números do teclado, mostra o maior no display e
imprime a soma:

```vic
[IMPOR] Display;

funct.(a,b).maior{
    if (a >= b) {
        return a
    } else {
        return b
    }
}

input.int(X)
input.int(Y)

CALL(maior, X, Y).M

Display1.show_digit(M)

ADD(X, Y).S
out.S
```

---

*Compilador: `VIC_interpreter.py` — Victor Interpreter Compiler*
