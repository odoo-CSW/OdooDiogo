from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
import requests
import logging
from . import constants

_logger = logging.getLogger(__name__)

class CSWTotalPayConfig(models.Model):
    _name = "x_csw_totalpay_config"
    _description = "Configurações do Conector CSW"
    _order = "x_studio_sequence asc, id asc"
    _rec_name = "x_studio_name"

    x_studio_avatar_image = fields.Binary(string="Avatar")

    x_studio_name = fields.Char(string="Empresa", readonly=True)
    x_studio_nome_empresa_abreviado = fields.Char(string="Nome Empresa Abreviado")

    x_studio_morada = fields.Char(string="Morada", readonly=True)
    x_studio_codigo_postal = fields.Char(string="Código Postal", readonly=True)
    x_studio_e_mail = fields.Char(string="E-mail", readonly=True)
    x_studio_mail_server_id = fields.Many2one(
        'ir.mail_server',
        string="Servidor de Email (Templates)",
        help="Servidor SMTP para envio de templates. Se não definido, usa o servidor padrão do Odoo."
    )
    x_studio_nif_empresa = fields.Char(string="NIF", readonly=True)

    x_studio_api_key = fields.Char(string="API Key", required=True)
    x_studio_odoo_api_key = fields.Char(string="API Key Odoo", required=True, password=True, help="API Key gerada no Odoo (Settings > Users > API Keys)")
    x_studio_odoo_user_id = fields.Many2one(
        'res.users',
        string="Utilizador Odoo",
        required=True,
        help="Utilizador associado à API Key Odoo. Este ID será enviado em todos os pedidos."
    )
    x_studio_token_sibs = fields.Text(string="Token SIBS", readonly=True)
    x_studio_organization_id = fields.Char(string="Organização ID", readonly=True)
    x_studio_url_request = fields.Char(string="URL Portal", readonly=True)
    x_studio_url_status_pagamento = fields.Char(string="URL Status Pagamento", readonly=True)
    x_studio_url_helper = fields.Char(string="URL Helper", required=True)

    x_studio_sequence = fields.Integer(string="Sequência", default=10)

    x_studio_status_check = fields.Selection(
        [
            ("active", "Ativo"),
            ("trial", "Trial"),
            ("inactive", "Inativo"),
            ("canceled", "Cancelado"),
            ("suspended", "Suspenso"),
            ("semValor", "Sem Valor"),
        ],
        string="Estado Integrador",
        default="semValor",
        readonly=True,
    )
    
    x_studio_payment_methods_active = fields.Char(
        string="Métodos de Pagamento Ativos",
        readonly=True,
        help="Métodos de pagamento disponíveis retornados pelo integrador"
    )
    
    # Campos de requisitos gerais (visíveis em todos os separadores)
    x_studio_tipo = fields.Selection(
        [
            ('Parceiro', 'PARCEIRO'),
            ('Cliente', 'CLIENTE'),
            ('Fornecedor', 'FORNECEDOR'),
            ('Interno', 'INTERNO'),
        ],
        string="Tipo",
        readonly=True
    )
    x_studio_entidade_pagamento = fields.Char(string="Entidade Pagamento", readonly=True)
    x_studio_secret_url = fields.Char(string="Secret URL", password=True, readonly=True)
    x_studio_secret_webhook = fields.Char(string="Secret Webhook", password=True, readonly=True)
    x_studio_secret_app = fields.Char(string="Secret App", password=True, readonly=True)
    x_studio_licenca = fields.Char(string="Licença", readonly=True)
    
    x_studio_multibanco_expiry_days = fields.Integer(
        string="Prazo validade referência (dias)",
        default=30,
        help="Número de dias de validade para referências Multibanco geradas pelo integrador."
    )

    @api.constrains('x_studio_multibanco_expiry_days')
    def _check_multibanco_expiry_days(self):
        for record in self:
            val = record.x_studio_multibanco_expiry_days
            if val is None:
                continue
            try:
                ival = int(val)
            except Exception:
                raise ValidationError(_("Prazo de validade Multibanco inválido."))
            if ival < 1 or ival > 365:
                raise ValidationError(_("O prazo de validade das referências Multibanco deve estar entre 1 e 365 dias."))
    # Terminal IDs e Cliente IDs por método de pagamento
    x_studio_paypal_terminal_id = fields.Integer(string="Terminal ID (PayPal)", readonly=True)
    x_studio_paypal_client_id = fields.Char(string="Cliente ID (PayPal)", readonly=True)
    
    x_studio_mbway_terminal_id = fields.Integer(string="Terminal ID (MB WAY)", readonly=True)
    x_studio_mbway_client_id = fields.Char(string="Cliente ID (MB WAY)", readonly=True)
    
    x_studio_multibanco_terminal_id = fields.Integer(string="Terminal ID (Multibanco)", readonly=True)
    x_studio_multibanco_client_id = fields.Char(string="Cliente ID (Multibanco)", readonly=True)
    
    x_studio_cartao_client_id = fields.Char(string="Cliente ID (Cartão)", readonly=True)
    
    # Campos de visibilidade (computados baseado nos métodos ativos)
    x_studio_show_paypal = fields.Boolean(string="Mostrar PayPal", compute="_compute_payment_methods_visibility", store=True)
    x_studio_show_mbway = fields.Boolean(string="Mostrar MB WAY", compute="_compute_payment_methods_visibility", store=True)
    x_studio_show_multibanco = fields.Boolean(string="Mostrar Multibanco", compute="_compute_payment_methods_visibility", store=True)
    x_studio_show_cartao = fields.Boolean(string="Mostrar Cartão", compute="_compute_payment_methods_visibility", store=True)
    
    x_studio_auto_reconcile = fields.Boolean(
        string="Reconciliação Automática",
        default=True,
        help="Ativa ou desativa a reconciliação automática na conta de Clientes Gerais quando o pagamento é aprovado"
    )
    
    x_studio_reconcile_account_id = fields.Many2one(
        'account.account',
        string="Conta de Reconciliação",
        domain="[('account_type', '=', 'asset_receivable')]",
        help="Conta contábil onde será feita a reconciliação automática quando o pagamento é aprovado"
    )
    
    @classmethod
    def _valid_field_parameter(cls, field, name):
        """Permitir parâmetro 'password' em campos Char."""
        return name == 'password' or super()._valid_field_parameter(field, name)
    
    @api.depends('x_studio_payment_methods_active')
    def _compute_payment_methods_visibility(self):
        """Computar visibilidade dos separadores baseado nos métodos ativos."""
        for record in self:
            methods = (record.x_studio_payment_methods_active or '').upper()
            record.x_studio_show_paypal = 'PAYPAL' in methods
            record.x_studio_show_mbway = 'MBWAY' in methods or 'MB WAY' in methods or 'MB_WAY' in methods
            record.x_studio_show_multibanco = 'MULTIBANCO' in methods
            record.x_studio_show_cartao = 'CARTAO' in methods or 'CARD' in methods or 'CARTÃO' in methods or 'CREDIT_CARD' in methods or 'CREDIT' in methods

    @api.model_create_multi
    def create(self, vals_list):
        """Garantir que apenas existe um registro de configuração."""
        if self.search_count([]) > 0:
            raise ValidationError(
                _("Apenas pode existir uma configuração de empresa. "
                  "Por favor, edite a configuração existente.")
            )
        return super(CSWTotalPayConfig, self).create(vals_list)

    @api.constrains('x_studio_auto_reconcile', 'x_studio_reconcile_account_id')
    def _check_reconcile_account(self):
        """Validar que a conta de reconciliação é obrigatória quando reconciliação automática está ativa."""
        for record in self:
            if record.x_studio_auto_reconcile and not record.x_studio_reconcile_account_id:
                raise ValidationError(
                    _("Por favor, selecione a Conta de Reconciliação quando a Reconciliação Automática estiver ativa.")
                )

    def action_test_odoo_webhook(self):
        """
        Testa o webhook odoo/webhook manualmente
        Envia x_organization_id e x_studio_key_api para validação
        """
        self.ensure_one()
        
        try:            # ====== VALIDAÇÕES OBRIGATÓRIAS ======
            if not self.x_studio_odoo_api_key:
                raise UserError(
                    _("\u274c API Key Odoo não está configurada!\n"
                      "Por favor, preencha a API Key Odoo (Settings > Users > API Keys) antes de testar a ligação.")
                )
            
            if not self.x_studio_odoo_user_id:
                raise UserError(
                    _("\u274c Utilizador Odoo não está selecionado!\n"
                      "Por favor, selecione o utilizador associado à API Key Odoo antes de testar a ligação.")
                )
            
            # URL base + odoo/webhook
            base_url = self.x_studio_url_helper
            if not base_url:
                raise UserError(
                    _("❌ URL Helper não está configurada!\n"
                      "Por favor, configure a URL Helper antes de testar o webhook.")
                )
            
            # Garantir que a URL termina com odoo/webhook
            if not base_url.endswith('/'):
                base_url += '/'
            webhook_url = base_url

            payload = {
                "_action": "Test Odoo Webhook",
                "_model": "x_csw_totalpay_config",
                "x_studio_key_api": self.x_studio_api_key or "",
                "x_studio_odoo_api_key": self.x_studio_odoo_api_key or "",
                "odoo_database": self.env.cr.dbname or "",
                "odoo_user_id": self.x_studio_odoo_user_id.id if self.x_studio_odoo_user_id else self.env.uid,
            }

            response = requests.post(webhook_url, json=payload, timeout=constants.HTTP_TIMEOUT_DEFAULT, verify=False)

            if response.status_code in [200, 201]:
                
                # Tentar parsear a resposta JSON
                try:
                    response_data = response.json()

                    _logger.info("Webhook Test %s", response_data)    

                    # Validar que response_data é um dicionário
                    if not isinstance(response_data, dict):
                        raise UserError(
                            _("❌ Resposta inválida do servidor!\n\n"
                              "A resposta recebida não é um JSON válido.\n"
                              "Tipo recebido: %s\n\n"
                              "Por favor, verifique a configuração do integrador.") % type(response_data).__name__
                        )
                    
                    # Verificar se tem o campo x_studio_status_check na resposta
                    # Pode estar no nível raiz ou dentro de 'data'
                    status_value = None
                    
                    if 'x_studio_status_check' in response_data:
                        status_value = response_data['x_studio_status_check']
                    elif 'data' in response_data and isinstance(response_data['data'], dict):
                        if 'x_studio_status_check' in response_data['data']:
                            status_value = response_data['data']['x_studio_status_check']
                    
                    if status_value:
                        # Validar se é um dos valores permitidos
                        allowed_values = ['active', 'trial', 'inactive', 'canceled', 'suspended', 'semValor']
                        if status_value in allowed_values:
                            # Preparar dados para atualização
                            update_vals = {'x_studio_status_check': status_value}
                            
                            # Verificar se tem paymentMethodsActive
                            payment_methods = None
                            if 'data' in response_data and isinstance(response_data['data'], dict):
                                if 'paymentMethodsActive' in response_data['data']:
                                    methods_list = response_data['data']['paymentMethodsActive']
                                    if isinstance(methods_list, list):
                                        payment_methods = ', '.join(methods_list)
                            
                            if payment_methods:
                                update_vals['x_studio_payment_methods_active'] = payment_methods
                            
                            # Verificar se tem dados da organização
                            organization_data = None
                            if 'organization' in response_data:
                                organization_data = response_data['organization']
                            elif 'data' in response_data and isinstance(response_data['data'], dict):
                                if 'organization' in response_data['data']:
                                    organization_data = response_data['data']['organization']
                            
                            if organization_data and isinstance(organization_data, dict):                                
                                # Mapear campos da organização para o modelo
                                # Chave: nome do campo na API | Valor: nome do campo no modelo
                                org_field_mapping = {
                                    'x_studio_organization_id': 'x_studio_organization_id',
                                    'organizationId': 'x_studio_organization_id',
                                    'x_name': 'x_studio_name',
                                    'name': 'x_studio_name',
                                    'x_studio_nome_empresa_abreviado': 'x_studio_nome_empresa_abreviado',
                                    'nomeEmpresaAbreviado': 'x_studio_nome_empresa_abreviado',
                                    'x_studio_morada': 'x_studio_morada',
                                    'morada': 'x_studio_morada',
                                    'x_studio_codigo_postal': 'x_studio_codigo_postal',
                                    'codigoPostal': 'x_studio_codigo_postal',
                                    'x_studio_e_mail': 'x_studio_e_mail',
                                    'email': 'x_studio_e_mail',
                                    'x_studio_nif_empresa': 'x_studio_nif_empresa',
                                    'nifEmpresa': 'x_studio_nif_empresa',
                                    'x_studio_url_request': 'x_studio_url_request',
                                    'urlRequest': 'x_studio_url_request',
                                    'x_studio_url_status_pagamento': 'x_studio_url_status_pagamento',
                                    'urlStatusPagamento': 'x_studio_url_status_pagamento',
                                    'x_studio_tipo': 'x_studio_tipo',
                                    'tipo': 'x_studio_tipo',
                                    'x_studio_entidade_pagamento': 'x_studio_entidade_pagamento',
                                    'entidadePagamento': 'x_studio_entidade_pagamento',
                                    'x_studio_licenca': 'x_studio_licenca',
                                    'licenca': 'x_studio_licenca',
                                    'x_studio_secret_url': 'x_studio_secret_url',
                                    'secretUrl': 'x_studio_secret_url',
                                    'x_studio_secret_webhook': 'x_studio_secret_webhook',
                                    'secretWebhook': 'x_studio_secret_webhook',
                                    'x_studio_secret_app': 'x_studio_secret_app',
                                    'secretApp': 'x_studio_secret_app',
                                    'x_studio_paypal_terminal_id': 'x_studio_paypal_terminal_id',
                                    'paypalTerminalId': 'x_studio_paypal_terminal_id',
                                    'x_studio_paypal_client_id': 'x_studio_paypal_client_id',
                                    'paypalClientId': 'x_studio_paypal_client_id',
                                    'x_studio_mbway_terminal_id': 'x_studio_mbway_terminal_id',
                                    'mbwayTerminalId': 'x_studio_mbway_terminal_id',
                                    'x_studio_mbway_client_id': 'x_studio_mbway_client_id',
                                    'mbwayClientId': 'x_studio_mbway_client_id',
                                    'x_studio_multibanco_terminal_id': 'x_studio_multibanco_terminal_id',
                                    'multibancoTerminalId': 'x_studio_multibanco_terminal_id',
                                    'x_studio_multibanco_client_id': 'x_studio_multibanco_client_id',
                                    'multibancoClientId': 'x_studio_multibanco_client_id',
                                    'x_studio_cartao_client_id': 'x_studio_cartao_client_id',
                                    'cartaoClientId': 'x_studio_cartao_client_id',
                                    'x_studio_token_sibs': 'x_studio_token_sibs',
                                    'tokenSibs': 'x_studio_token_sibs',
                                }
                                
                                for org_field, model_field in org_field_mapping.items():
                                    if org_field in organization_data:
                                        value = organization_data[org_field]
                                        # Atualizar mesmo que seja string vazia (para limpar campos)
                                        if value is not None:
                                            # Normalizar campo tipo para formato correto
                                            if model_field == 'x_studio_tipo' and value:
                                                value = value.capitalize()  # CLIENTE -> Cliente
                                            # Manter números como integer para campos terminal_id
                                            elif model_field in ['x_studio_paypal_terminal_id', 'x_studio_mbway_terminal_id', 'x_studio_multibanco_terminal_id']:
                                                if isinstance(value, str) and value.isdigit():
                                                    value = int(value)
                                                elif not isinstance(value, int):
                                                    continue  # Pular se não for número válido
                                            # Converter outros números para string
                                            elif isinstance(value, (int, float)):
                                                value = str(value)
                                            update_vals[model_field] = value
                            
                            # Se x_studio_nome_empresa_abreviado não veio na API mas x_studio_name veio
                            # copiar x_studio_name para x_studio_nome_empresa_abreviado (se estiver vazio)
                            if 'x_studio_name' in update_vals and 'x_studio_nome_empresa_abreviado' not in update_vals:
                                if not self.x_studio_nome_empresa_abreviado:
                                    update_vals['x_studio_nome_empresa_abreviado'] = update_vals['x_studio_name']
                            
                            # Atualizar os campos no registro
                            self.write(update_vals)
                            
                            # Forçar recompute dos campos de visibilidade
                            if 'x_studio_payment_methods_active' in update_vals:
                                self._compute_payment_methods_visibility()
                            
                            # Atualizar campo certificação no suporte se veio na resposta
                            certificacao_value = None
                            if organization_data and isinstance(organization_data, dict):
                                certificacao_value = organization_data.get('x_studio_support_certificacao') or organization_data.get('certificacao')
                                                    
                            if certificacao_value:
                                try:
                                    suporte = self.env['x_csw_totalpay_suporte'].sudo().get_suporte_info()                                    
                                    suporte.sudo().write({'x_studio_support_certificacao': certificacao_value})
                                except Exception as e:
                                    _logger.error("[Validar Licença] ❌ Erro ao atualizar certificação no suporte: %s", str(e))
                                                        
                            # Notificação de sucesso por 3 segundos, depois reload
                            return {
                                'type': 'ir.actions.client',
                                'tag': 'totalpay_notification_reload',
                                'params': {
                                    'title': _('Licença validada'),
                                    'message': _('Configuração atualizada com sucesso.'),
                                    'type': 'success',
                                    'timeout': 2000,
                                },
                            }

                except ValueError:
                    _logger.warning("[Validar Licença] Resposta inválida (não é JSON)")
                
            else:
                # Se o webhook não retornar 200/201, tentar obter o estado do body;
                # se não for possível obter um estado válido, definir como 'canceled'.
                try:
                    try:
                        response_data = response.json()
                    except Exception:
                        response_data = None

                    status_value = None
                    if isinstance(response_data, dict):
                        if 'x_studio_status_check' in response_data:
                            status_value = response_data['x_studio_status_check']
                        elif 'data' in response_data and isinstance(response_data['data'], dict):
                            status_value = response_data['data'].get('x_studio_status_check')

                    allowed_values = ['active', 'trial', 'inactive', 'canceled', 'suspended', 'semValor']
                    if status_value in allowed_values:
                        write_status = status_value
                    else:
                        write_status = 'semValor'

                    try:
                        self.write({'x_studio_status_check': write_status})
                    except Exception as e:
                        _logger.error("[Validar Licença] Erro ao atualizar x_studio_status_check: %s", e)

                    # Extrair mensagem para notificação (priorizar campo 'message' do body)
                    try:
                        if isinstance(response_data, dict):
                            error_message = response_data.get('message') or (response_data.get('data') or {}).get('message')
                        else:
                            error_message = None
                        if not error_message:
                            error_message = response.text[:200]
                    except Exception:
                        error_message = response.text[:200]

                except Exception as e:
                    _logger.error("[Validar Licença] Erro ao processar resposta de erro: %s", e)
                    try:
                        self.write({'x_studio_status_check': 'semValor'})
                    except Exception:
                        pass
                    error_message = response.text[:200] if hasattr(response, 'text') else str(e)

                # Registar body completo nos logs para debug
                _logger.error("[Validar Licença] Erro ao validar webhook: %s | Body: %s", error_message, response.text if hasattr(response, 'text') else '')

                # Notificação simples com apenas a mensagem de erro, espera 3s e depois reload
                return {
                    'type': 'ir.actions.client',
                    'tag': 'totalpay_notification_reload',
                    'params': {
                        'title': _('Erro ao validar licença'),
                        'message': error_message or _('Ocorreu um erro ao validar a licença.'),
                        'type': 'danger',
                        'timeout': 3000,
                    },
                }

        except requests.exceptions.RequestException as e:
            _logger.error("[Validar Licença] Erro ao conectar ao webhook: %s", str(e))
            try:
                self.write({'x_studio_status_check': 'semValor'})
            except Exception as e2:
                _logger.error("[Validar Licença] Erro ao atualizar x_studio_status_check para semValor: %s", e2)
            
            # Retornar notificação em vez de lançar UserError
            return {
                'type': 'ir.actions.client',
                'tag': 'totalpay_notification_reload',
                'params': {
                    'title': _('❌ Erro ao conectar ao webhook'),
                    'message': _('Não foi possível conectar ao serviço TotalPay. Configuração marcada como indisponível.'),
                    'type': 'danger',
                    'timeout': 5000,
                },
            }
        except Exception as e:
            _logger.error("[Validar Licença] Erro inesperado ao executar webhook: %s", str(e))
            try:
                self.write({'x_studio_status_check': 'semValor'})
            except Exception:
                pass
            
            return {
                'type': 'ir.actions.client',
                'tag': 'totalpay_notification_reload',
                'params': {
                    'title': _('❌ Erro ao executar webhook'),
                    'message': _('Ocorreu um erro inesperado. Configuração marcada como indisponível.'),
                    'type': 'danger',
                    'timeout': 5000,
                },
            }

