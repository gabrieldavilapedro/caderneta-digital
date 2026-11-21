class RegraDeNegocioError(Exception):
    """Operação recusada por uma regra de negócio.

    `campo` indica qual campo do formulário causou o problema (ou None, para
    uma mensagem geral). Usada pelos serviços da caderneta e do estoque.
    """

    def __init__(self, mensagem, campo=None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.campo = campo
