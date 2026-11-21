/*
 * Caderneta Digital — melhorias de usabilidade.
 * Tudo aqui é opcional: sem JavaScript, os formulários continuam funcionando
 * e todas as validações são feitas novamente no servidor.
 */
(() => {
  'use strict'

  // --- Utilitários ---------------------------------------------------------

  const formatoMoeda = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })
  const moeda = (centavos) => formatoMoeda.format(centavos / 100)
  const valorParaCampo = (centavos) =>
    (centavos / 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, useGrouping: false })

  /**
   * Ponto sem vírgula: em dinheiro (2 casas), "1.234" só pode ser milhar; em quantidades
   * (3 casas), um único ponto é decimal ("0.350" kg). Mesma regra de caderneta/utils.py.
   */
  function pontoEhMilhar(texto, casas) {
    return /^[1-9]\d{0,2}(\.\d{3})+$/.test(texto) && (casas < 3 || texto.split('.').length > 2)
  }

  /** "12,50" -> 1250 (casas=2); "0,35" -> 350 (casas=3). Retorna null se inválido. */
  function lerEscalado(texto, casas) {
    let s = String(texto || '').replace(/R\$|\s/g, '')
    if (!s) return null
    if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.')
    else if (pontoEhMilhar(s, casas)) s = s.replace(/\./g, '')
    if (!new RegExp(`^\\d+(\\.\\d{1,${casas}})?$`).test(s)) return null
    const [inteiro, decimais = ''] = s.split('.')
    return Number(inteiro) * 10 ** casas + Number(decimais.padEnd(casas, '0'))
  }
  const lerCentavos = (texto) => lerEscalado(texto, 2)

  function formatarTelefone(valor) {
    const d = String(valor || '').replace(/\D/g, '').slice(0, 11)
    if (d.length <= 2) return d
    if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`
    if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`
    return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`
  }

  // --- Máscaras: campos numéricos aceitam apenas números e uma vírgula ----------

  const MASCARAS_NUMERICAS = {
    dinheiro: { inteiros: 8, decimais: 2 },
    quantidade: { inteiros: 6, decimais: 3 },
  }

  function normalizarNumero(texto, { inteiros, decimais }) {
    let s = String(texto || '').replace(/R\$|\s/g, '')
    if (s.includes(',')) s = s.replace(/\./g, '') // "1.234,56" -> "1234,56"
    else if (pontoEhMilhar(s, decimais)) s = s.replace(/\./g, '') // dinheiro: "1.234" -> "1234"
    s = s.replace('.', ',').replace(/[^\d,]/g, '') // "0.350" -> "0,350"
    const [inteiro, ...resto] = s.split(',')
    return inteiro.slice(0, inteiros) + (resto.length ? `,${resto.join('').slice(0, decimais)}` : '')
  }

  function aplicarMascaras(raiz) {
    raiz.querySelectorAll('input[data-mascara]').forEach((campo) => {
      if (campo.dataset.mascaraAtiva) return
      campo.dataset.mascaraAtiva = '1'

      if (campo.dataset.mascara === 'telefone') {
        campo.addEventListener('input', () => { campo.value = formatarTelefone(campo.value) })
        return
      }
      const config = MASCARAS_NUMERICAS[campo.dataset.mascara]
      if (!config) return
      const permitido = new RegExp(`^\\d{0,${config.inteiros}}([,.]\\d{0,${config.decimais}})?$`)

      // Bloqueia a tecla antes de aparecer (letras, segunda vírgula, casas decimais a mais).
      // Textos com mais de um caractere (colar, sugestão do teclado) são limpos no "input".
      campo.addEventListener('beforeinput', (evento) => {
        if (!evento.inputType.startsWith('insert') || evento.data?.length !== 1) return
        const { selectionStart: inicio, selectionEnd: fim, value } = campo
        if (!permitido.test(value.slice(0, inicio) + evento.data + value.slice(fim))) evento.preventDefault()
      })
      campo.addEventListener('input', () => {
        const original = campo.value
        const normalizado = normalizarNumero(original, config)
        if (normalizado === original) return
        const posicao = Math.max(0, (campo.selectionStart ?? normalizado.length) - (original.length - normalizado.length))
        campo.value = normalizado
        campo.setSelectionRange(posicao, posicao)
      })
    })
  }
  aplicarMascaras(document)

  // --- Comportamentos gerais ---------------------------------------------------

  // Impede envio duplicado (ex.: clique duplo em "Confirmar venda").
  document.querySelectorAll('form[data-envio-unico]').forEach((form) => {
    form.addEventListener('submit', () => {
      form.querySelectorAll('button[type="submit"]').forEach((botao) => {
        botao.disabled = true
        botao.insertAdjacentHTML('afterbegin', '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>')
      })
    })
  })

  // Selects de filtro aplicam o filtro ao mudar.
  document.querySelectorAll('select[data-enviar-ao-mudar]').forEach((select) => {
    select.addEventListener('change', () => select.form.requestSubmit())
  })

  // Botão de impressão do extrato.
  document.querySelectorAll('[data-imprimir]').forEach((botao) => {
    botao.addEventListener('click', () => window.print())
  })

  // Mensagens de sucesso somem sozinhas depois de alguns segundos.
  document.querySelectorAll('.alert-success[data-fechar-auto]').forEach((alerta) => {
    setTimeout(() => window.bootstrap?.Alert.getOrCreateInstance(alerta).close(), 6000)
  })

  // Ao corrigir um campo que veio com erro do servidor, remove o destaque vermelho.
  const limparErro = (campo) => campo.classList.remove('is-invalid')
  document.querySelectorAll('.is-invalid').forEach((campo) => {
    campo.addEventListener('input', () => limparErro(campo), { once: true })
    campo.addEventListener('change', () => limparErro(campo), { once: true })
  })

  // --- Venda a prazo / pagamento: prévia do novo saldo ------------------------------

  const formLancamento = document.querySelector('form[data-lancamento]')
  if (formLancamento) {
    const ehVenda = formLancamento.dataset.tipo === 'VENDA'
    const saldoAtual = Number(formLancamento.dataset.saldoCentavos) || 0
    const campoValor = formLancamento.querySelector('input[name="valor"]')
    const previa = document.getElementById('previa')
    const previaValor = document.getElementById('previa-valor')
    const previaNovoSaldo = document.getElementById('previa-novo-saldo')
    const previaAviso = document.getElementById('previa-aviso')
    const avisoValorCalculado = document.getElementById('valor-calculado')

    const atualizar = () => {
      const centavos = lerCentavos(campoValor.value)
      previa.hidden = false
      if (centavos === null || centavos <= 0) {
        previaValor.textContent = '—'
        previaNovoSaldo.textContent = '—'
        previaAviso.hidden = true
        return
      }
      const novoSaldo = saldoAtual + (ehVenda ? centavos : -centavos)
      previaValor.textContent = `${ehVenda ? '+' : '−'} ${moeda(centavos)}`
      previaNovoSaldo.textContent = moeda(novoSaldo)
      previaNovoSaldo.classList.toggle('text-danger', novoSaldo < 0)
      previaAviso.hidden = novoSaldo >= 0
    }

    campoValor.addEventListener('input', atualizar)
    campoValor.addEventListener('blur', () => {
      const centavos = lerCentavos(campoValor.value)
      if (centavos !== null) campoValor.value = valorParaCampo(centavos)
    })
    // Pagamento: escolher o mês que o cliente está pagando (inteiro ou só uma parte).
    // Os meses mais antigos são quitados primeiro, então o valor é distribuído do mês
    // mais antigo para o mais recente, e a tela mostra o que fica quitado e o que fica faltando.
    const campoDescricao = formLancamento.querySelector('input[name="descricao"]')
    const resumoDistribuicao = document.getElementById('distribuicao-pagamento')
    const meses = [...formLancamento.querySelectorAll('[data-pagar-mes]')].map((elemento) => ({
      elemento,
      nome: elemento.dataset.nome,
      emAberto: Number(elemento.dataset.emAbertoCentavos),
      situacao: elemento.querySelector('[data-mes-situacao]'),
    }))
    let descricaoAutomatica = '' // última descrição gerada pela tela (substituível)

    const distribuir = (centavos) => {
      let resto = centavos
      const quitados = []
      let parcial = null
      for (const mes of meses) {
        if (resto <= 0) break
        if (resto >= mes.emAberto) {
          quitados.push(mes)
          resto -= mes.emAberto
        } else {
          parcial = { mes, falta: mes.emAberto - resto }
          resto = 0
        }
      }
      return { quitados, parcial }
    }

    const descreverPagamento = ({ quitados, parcial }) => {
      let texto = ''
      if (quitados.length === 1) texto = `Pagamento da fatura de ${quitados[0].nome}`
      if (quitados.length > 1) texto = `Pagamento das faturas de ${quitados[0].nome} a ${quitados.at(-1).nome}`
      if (parcial) {
        texto = texto ? `${texto} e parte de ${parcial.mes.nome}` : `Pagamento parcial da fatura de ${parcial.mes.nome}`
      }
      return texto
    }

    const mostrarDistribuicao = () => {
      if (!meses.length) return
      const centavos = lerCentavos(campoValor.value) || 0
      const distribuicao = distribuir(centavos)
      const { quitados, parcial } = distribuicao
      meses.forEach((mes) => {
        const quitado = quitados.includes(mes)
        const ehParcial = parcial?.mes === mes
        mes.elemento.classList.toggle('active', quitado)
        mes.elemento.classList.toggle('list-group-item-warning', ehParcial)
        if (quitado || ehParcial) mes.elemento.setAttribute('aria-current', 'true')
        else mes.elemento.removeAttribute('aria-current')
        mes.situacao.textContent = quitado ? '✓ será quitada' : ehParcial ? `parcial: faltarão ${moeda(parcial.falta)}` : ''
      })

      if (resumoDistribuicao) {
        let resumo = ''
        if (quitados.length) resumo = `Quita ${quitados.map((m) => m.nome).join(', ')}`
        if (parcial) {
          resumo = resumo
            ? `${resumo} e paga parte de ${parcial.mes.nome} (faltarão ${moeda(parcial.falta)}).`
            : `Pagamento parcial de ${parcial.mes.nome}: faltarão ${moeda(parcial.falta)}.`
        } else if (resumo) {
          resumo += '.'
        }
        resumoDistribuicao.textContent = resumo
        resumoDistribuicao.hidden = !resumo
      }

      // Descrição automática, a menos que o comerciante tenha escrito outra.
      if (campoDescricao && (!campoDescricao.value || campoDescricao.value === descricaoAutomatica)) {
        descricaoAutomatica = descreverPagamento(distribuicao)
        campoDescricao.value = descricaoAutomatica
      }
    }

    meses.forEach((mes) => {
      mes.elemento.addEventListener('click', (evento) => {
        evento.preventDefault() // sem JavaScript, o link recarrega a página já preenchida
        campoValor.value = mes.elemento.dataset.valor
        limparErro(campoValor)
        atualizar()
        mostrarDistribuicao()
      })
    })
    campoValor.addEventListener('input', mostrarDistribuicao) // pagar só uma parte
    // Descrição que veio preenchida pelo servidor (link do mês) conta como automática.
    descricaoAutomatica = descreverPagamento(distribuir(lerCentavos(campoValor.value) || 0))
    mostrarDistribuicao()

    formLancamento.querySelector('[data-quitar]')?.addEventListener('click', (evento) => {
      campoValor.value = evento.currentTarget.dataset.quitar
      limparErro(campoValor)
      atualizar()
      mostrarDistribuicao()
      campoValor.focus()
    })

    // Com produtos do estoque escolhidos, o valor é calculado e fica somente leitura.
    formLancamento.addEventListener('itens:alterados', (evento) => {
      const { totalCentavos, algum } = evento.detail
      if (algum) {
        campoValor.readOnly = true
        campoValor.value = totalCentavos > 0 ? valorParaCampo(totalCentavos) : ''
        limparErro(campoValor)
      } else if (campoValor.readOnly) {
        campoValor.readOnly = false
        campoValor.value = ''
      }
      if (avisoValorCalculado) avisoValorCalculado.hidden = !algum
      atualizar()
    })
    atualizar()
  }

  // --- Produtos de uma venda (venda à vista e venda a prazo) ---------------------

  document.querySelectorAll('[data-itens]').forEach((bloco) => {
    const lista = bloco.querySelector('[data-itens-lista]')
    const modelo = bloco.querySelector('template[data-itens-modelo]')
    const totalFormularios = bloco.querySelector('input[name$="-TOTAL_FORMS"]')
    const saidaTotal = bloco.querySelector('[data-itens-total]')
    const botaoAdicionar = bloco.querySelector('[data-itens-adicionar]')
    botaoAdicionar.hidden = false // só faz sentido com JavaScript

    const calcular = () => {
      let total = 0
      let algum = false
      lista.querySelectorAll('[data-item]').forEach((linha) => {
        const opcao = linha.querySelector('select').selectedOptions[0]
        const escolhido = Boolean(opcao && opcao.value)
        const milesimos = lerEscalado(linha.querySelector('input[data-mascara="quantidade"]').value, 3)
        const infoEstoque = linha.querySelector('[data-item-estoque]')
        // Guarda o preço que está na tela; se ele mudar antes de confirmar, o servidor avisa.
        const precoExibido = linha.querySelector('input[name$="-preco_exibido"]')
        if (precoExibido) precoExibido.value = escolhido ? opcao.dataset.precoCentavos : ''
        let subtotal = 0

        linha.querySelector('[data-item-unidade]').textContent = escolhido ? opcao.dataset.sigla : 'un'
        if (escolhido) {
          algum = true
          const falta = milesimos !== null && milesimos > Math.round(Number(opcao.dataset.estoque) * 1000)
          infoEstoque.textContent = falta
            ? `Estoque insuficiente: disponível ${opcao.dataset.estoqueTexto}`
            : `Em estoque: ${opcao.dataset.estoqueTexto}`
          infoEstoque.classList.toggle('text-danger', falta)
          if (milesimos) subtotal = Math.round((Number(opcao.dataset.precoCentavos) * milesimos) / 1000)
        } else {
          infoEstoque.textContent = ''
          infoEstoque.classList.remove('text-danger')
        }
        linha.querySelector('[data-item-subtotal]').textContent = subtotal ? moeda(subtotal) : '—'
        total += subtotal
      })
      saidaTotal.textContent = algum ? moeda(total) : '—'
      bloco.dispatchEvent(new CustomEvent('itens:alterados', { bubbles: true, detail: { totalCentavos: total, algum } }))
    }

    botaoAdicionar.addEventListener('click', () => {
      const indice = Number(totalFormularios.value)
      lista.insertAdjacentHTML('beforeend', modelo.innerHTML.replace(/__prefix__/g, String(indice)))
      totalFormularios.value = String(indice + 1)
      const novaLinha = lista.lastElementChild
      aplicarMascaras(novaLinha)
      novaLinha.querySelector('select').focus()
      calcular()
    })

    lista.addEventListener('click', (evento) => {
      const botao = evento.target.closest('[data-item-remover]')
      if (!botao) return
      const linha = botao.closest('[data-item]')
      if (lista.querySelectorAll('[data-item]').length === 1) {
        // Última linha: apenas limpa (o formulário precisa de ao menos uma).
        linha.querySelector('select').value = ''
        linha.querySelector('input[data-mascara="quantidade"]').value = ''
      } else {
        linha.remove()
      }
      calcular()
    })

    bloco.addEventListener('input', calcular)
    bloco.addEventListener('change', calcular)
    calcular()
  })
})()
