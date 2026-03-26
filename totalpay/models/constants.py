# -*- coding: utf-8 -*-

"""
Constantes do módulo CSW Connector
"""

# Stage IDs do CSW Connector
STAGE_APROVADO = 1
STAGE_PENDENTE = 2
STAGE_CANCELADO = 3
STAGE_REEMBOLSO = 4
STAGE_EM_PROCESSAMENTO = 5
STAGE_FALHOU = 6

# Métodos de Pagamento
PAYMENT_METHOD_MBWAY = 'MBWAY'
PAYMENT_METHOD_MULTIBANCO = 'MULTIBANCO'

# Timeouts HTTP (em segundos)
HTTP_TIMEOUT_DEFAULT = 10  # Timeout padrão para requests
HTTP_TIMEOUT_PAYMENT = 30  # Timeout para pedidos de pagamento

# Timeouts MB WAY
MBWAY_TIMEOUT_MINUTES = 4  # 4 minutos para expiração MB WAY

# Delays e intervalos (em segundos)
RETRY_DELAY = 2  # Delay entre tentativas
WEBHOOK_DELAY = 1  # Delay antes de enviar webhook
BATCH_WAIT_INTERVAL = 0.3  # Intervalo entre processamento de lotes

# Rate Limit Payments
RATE_LIMIT_PAYMENTS = 5  # Limite de 5 requisições por bloco