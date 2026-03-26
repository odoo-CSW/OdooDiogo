import logging
import re
from datetime import datetime, timedelta

import requests
from odoo import models, _
from odoo.exceptions import UserError

from . import constants

_logger = logging.getLogger(__name__)

_API_STAGE_MAP = {
    'COMPLETED': constants.STAGE_APROVADO,
    'APPROVED': constants.STAGE_APROVADO,
    'SUCCESS': constants.STAGE_APROVADO,
    'PROCESSING': constants.STAGE_EM_PROCESSAMENTO,
    'PENDING': constants.STAGE_PENDENTE,
    'FAILED': constants.STAGE_FALHOU,
    'DECLINED': constants.STAGE_FALHOU,
    'CANCELLED': constants.STAGE_CANCELADO,
    'CANCELED': constants.STAGE_CANCELADO,
    'REFUNDED': constants.STAGE_REEMBOLSO,
}


class CSWTotalPayIntegrator(models.Model):
    _inherit = "x_csw_totalpay"

    def action_check_payment_status_from_integrator(self):
        self._ensure_single('Verificar Status')
        try:
            # Permitir atualização de status para Pendente, Em Processamento, ou Falhou
            # (Falhou pode ser atualizado para Cancelado se a API retornar esse status)
            if self.x_studio_stage_id.id not in (constants.STAGE_PENDENTE, constants.STAGE_EM_PROCESSAMENTO, constants.STAGE_FALHOU):
                return {'status': 'skipped', 'message': 'Pagamento não está em estado atualizável'}

            config = self._get_config()
            if not config:
                return {'status': 'error', 'message': 'Configuração não encontrada'}

            status_url_base = config.x_studio_url_status_pagamento
            if not status_url_base:
                return {'status': 'error', 'message': 'URL Status Pagamento não configurado'}

            base = status_url_base if status_url_base.endswith('/') else status_url_base + '/'
            status_url = f"{base}{self.x_api_transaction_id}"
            headers = {'x-api-key': config.x_studio_api_key, 'Content-Type': 'application/json'}
            response = requests.get(status_url, headers=headers, timeout=constants.HTTP_TIMEOUT_DEFAULT, verify=False)

            if response.status_code != 200:
                return {'status': 'error', 'message': f'HTTP {response.status_code}'}

            response_data = response.json()
            if not response_data.get('success'):
                return {'status': 'error', 'message': 'API retornou erro'}

            data = response_data.get('data', {})
            api_status = data.get('status', '').upper()
            new_stage_id = _API_STAGE_MAP.get(api_status)
            if not new_stage_id:
                return {'status': 'unknown_status', 'message': f'Status {api_status} desconhecido'}

            if new_stage_id == self.x_studio_stage_id.id:
                return {'status': 'unchanged', 'current_stage_id': self.x_studio_stage_id.id, 'api_status': api_status, 'message': 'Status não mudou'}

            update_vals = {'x_studio_stage_id': new_stage_id, 'x_studio_state_integrator': api_status}
            completed_at = (data.get('timestamps') or {}).get('completedAt')
            if completed_at and new_stage_id == constants.STAGE_APROVADO:
                try:
                    dt = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                    update_vals['x_studio_date_hour_payment_approved'] = dt.replace(tzinfo=None)
                except Exception:
                    pass
            if data.get('sibsTransactionId'):
                update_vals['x_studio_paypal_transaction_id'] = data['sibsTransactionId']

            self.with_context(from_api=True).write(update_vals)
            return {
                'status': 'updated',
                'new_stage_id': new_stage_id,
                'stage_name': self.x_studio_stage_id.x_name,
                'api_status': api_status,
                'message': f'Status atualizado para {api_status}',
            }

        except Exception as e:
            _logger.error("[CHECK STATUS] Erro %s: %s", self.x_name, str(e), exc_info=True)
            return {'status': 'error', 'message': 'Erro ao verificar status do pagamento'}

    def action_create_payment_request(self):
        self.ensure_one()
        if self.env.context.get('defer_totalpay_processing'):
            return True
        try:
            config = self._get_config()
            if not config:
                self._set_payment_failed("Configuração Total Pay não encontrada!")
                return False

            if any((self.x_studio_mb_referencia, self.x_studio_payment_url, self.x_studio_paypal_transaction_id, self.x_api_transaction_id)):
                return True

            if config.x_studio_status_check not in ['active', 'trial']:
                self._set_payment_failed(f"Integrador não está ativo! Estado atual: {config.x_studio_status_check}")
                return False

            if not config.x_studio_odoo_api_key or not config.x_studio_odoo_user_id:
                self._set_payment_failed("Credenciais Odoo incomplete!")
                return False

            _logger.info("[VALIDAÇÃO] Credenciais OK - User: %s", config.x_studio_odoo_user_id.name)

            url_request = config.x_studio_url_request
            if not url_request or not url_request.startswith(('http://', 'https://')):
                self._set_payment_failed("URL Request inválida!")
                return False

            payment_method = (self.x_studio_metodo_pagamento or "").upper()
            invoice_ref = self.account_payment_id.name or self.x_name if self.account_payment_id else self.x_name
            payment_description = config.x_studio_nome_empresa_abreviado or "Pagamento"

            payment_amount = self.x_studio_value
            if not payment_amount or payment_amount <= 0:
                error_msg = f"Valor do pagamento inválido: {payment_amount}. O valor deve ser maior que 0."
                _logger.error("[CRIAR PEDIDO] %s (Connector ID: %s, Payment ID: %s, Payment Name: %s)",
                             error_msg, self.id,
                             self.account_payment_id.id if self.account_payment_id else 'N/A',
                             self.account_payment_id.name if self.account_payment_id else 'N/A')
                self._set_payment_failed(error_msg)
                raise UserError(_(error_msg))

            _logger.info("[CRIAR PEDIDO] Pagamento %s - Valor: %.2f %s",
                        invoice_ref, payment_amount,
                        self.x_studio_currency_id.name if self.x_studio_currency_id else 'EUR')

            base_payload = {
                "x_studio_key_api": config.x_studio_api_key,
                "x_studio_odoo_api_key": config.x_studio_odoo_api_key or "",
                "x_studio_metodo_pagamento": payment_method,
                "merchantTransactionId": invoice_ref,
                "amount": payment_amount,
                "description": payment_description,
                "x_studio_url_status_pagamento": config.x_studio_url_status_pagamento or "",
                "odoo_database": self.env.cr.dbname or "",
                "odoo_user_id": config.x_studio_odoo_user_id.id,
            }
            if config.x_studio_organization_id:
                base_payload["x_organization_id"] = config.x_studio_organization_id
            if config.x_studio_entidade_pagamento:
                base_payload["entity"] = config.x_studio_entidade_pagamento

            if payment_method == constants.PAYMENT_METHOD_MBWAY:
                payment = self.account_payment_id
                phone_number = (
                    (payment.x_mbway_phone if payment else None)
                    or self.x_studio_partner_phone
                    or (payment.partner_id.phone if payment and payment.partner_id else None)
                )
                if not phone_number:
                    error_msg = "Número de telefone obrigatório para MB WAY!"
                    self._set_payment_failed(error_msg)
                    return self._notify(_('❌ Telefone Obrigatório'), error_msg, 'danger')

                phone = re.sub(r'[^\d]', '', phone_number)
                if phone.startswith('00351') and len(phone) > 9:
                    phone = phone[-9:]
                elif phone.startswith('351') and len(phone) > 9:
                    phone = phone[-9:]

                if not re.match(r'^9\d{8}$', phone):
                    error_msg = f"Número MB WAY inválido! Deve ter 9 dígitos e começar com 9. Telefone recebido: {phone_number}"
                    self._set_payment_failed(error_msg)
                    return self._notify(_('❌ Telefone Inválido'), error_msg, 'danger')

                formatted_phone = f"351#{phone}"
                _logger.info("[MBWAY] Telefone formatado: %s", formatted_phone)
                base_payload["phoneNumber"] = formatted_phone

            elif payment_method == "PAYPAL":
                if not self.x_studio_partner_email:
                    error_msg = "E-mail obrigatório para PayPal!"
                    self._set_payment_failed(error_msg)
                    return self._notify(_('❌ Email Obrigatório'), error_msg, 'danger')
                base_payload["paypal_email"] = self.x_studio_partner_email

            elif payment_method == constants.PAYMENT_METHOD_MULTIBANCO:
                base_payload["expiryDays"] = config.x_studio_multibanco_expiry_days or 3
            elif payment_method == "CREDIT_CARD":
                base_payload["card_payment"] = True
            else:
                error_msg = f"Método de pagamento '{payment_method}' não suportado! Métodos válidos: MBWAY, PAYPAL, MULTIBANCO, CREDIT_CARD"
                self._set_payment_failed(error_msg)
                return self._notify(_('❌ Método Não Suportado'), error_msg, 'danger')

            response = requests.post(url_request, json=base_payload, timeout=constants.HTTP_TIMEOUT_PAYMENT, verify=False)

            if response.status_code in [200, 201]:
                try:
                    response_data = response.json()

                    if response_data.get('success', True) is False:
                        error_message = (
                            response_data.get('message') or
                            response_data.get('error') or
                            response_data.get('detail') or
                            'Erro ao criar pagamento'
                        )
                        _logger.error("[CRIAR PEDIDO] Erro %s: %s", self.x_name, error_message)
                        self._set_payment_failed(f"HTTP {response.status_code}: {error_message}")
                        return self._notify('❌ Erro ao Criar Pagamento', _('Não foi possível concluir o pagamento. Por favor, tente novamente.'), 'danger')

                    update_vals = {}
                    d = response_data.get('data') if isinstance(response_data.get('data'), dict) else {}
                    payment_details = d.get('paymentDetails') if isinstance(d.get('paymentDetails'), dict) else {}

                    payment_api_id = (
                        response_data.get('id')
                        or d.get('id')
                        or d.get('paymentId')
                        or d.get('payment_id')
                        or payment_details.get('transactionId')
                        or payment_details.get('id')
                    )
                    if payment_api_id:
                        update_vals['x_api_transaction_id'] = str(payment_api_id)

                    api_status = response_data.get('status') or payment_details.get('status')
                    if api_status:
                        new_stage = _API_STAGE_MAP.get(api_status.upper())
                        if new_stage:
                            update_vals['x_studio_stage_id'] = new_stage

                    payment_url = response_data.get('payment_url') or d.get('payment_url')
                    if payment_url:
                        update_vals['x_studio_payment_url'] = payment_url

                    if payment_method == constants.PAYMENT_METHOD_MULTIBANCO:
                        # Sempre guardar número de dias de validade usado (independente da resposta da API)
                        update_vals['x_studio_mb_expiry_days'] = config.x_studio_multibanco_expiry_days or 30
                        
                        pd = payment_details or response_data

                        if isinstance(pd, dict):
                            if 'entity' in pd:
                                update_vals['x_studio_mb_entidade'] = str(pd['entity'])
                            if 'reference' in pd:
                                update_vals['x_studio_mb_referencia'] = str(pd['reference'])
                            if 'amount' in pd:
                                update_vals['x_studio_mb_valor'] = pd['amount']
                            if 'expiryDate' in pd:
                                try:
                                    expiry_clean = pd['expiryDate'].replace('Z', '+00:00').split('.')[0] + '.000000+00:00'
                                    update_vals['x_studio_mb_expiry_date'] = datetime.fromisoformat(expiry_clean).replace(tzinfo=None)
                                except Exception:
                                    pass

                    elif payment_method == "PAYPAL":
                        update_vals['x_studio_paypal_transaction_id'] = response_data.get('transaction_id')
                        update_vals['x_studio_paypal_capture_id'] = response_data.get('capture_id')
                    elif payment_method == constants.PAYMENT_METHOD_MBWAY:
                        # Procurar timestamp da API em múltiplos locais possíveis
                        timestamp_keys = ('startedAt', 'startAt', 'createdAt', 'created_at', 'initiatedAt', 'createdOn', 'timestamp')
                        start_value = None
                        for container in (response_data, d):
                            if not isinstance(container, dict):
                                continue
                            timestamps = container.get('timestamps') if isinstance(container.get('timestamps'), dict) else {}
                            for key in timestamp_keys:
                                if timestamps.get(key):
                                    start_value = timestamps.get(key)
                                    break
                            if start_value:
                                break
                            for key in timestamp_keys:
                                if container.get(key):
                                    start_value = container.get(key)
                                    break
                            if start_value:
                                break

                        # Tentar converter o timestamp da API
                        start_dt = None
                        if start_value:
                            try:
                                if isinstance(start_value, str):
                                    parsed = datetime.fromisoformat(start_value.replace('Z', '+00:00'))
                                    start_dt = parsed.replace(tzinfo=None)
                            except Exception:
                                start_dt = None

                        # Se não conseguiu obter timestamp da API, usar o momento atual como fallback
                        if not start_dt:
                            start_dt = datetime.now()
                            _logger.warning("[MBWAY] Timestamp da API não encontrado, usando timestamp local: %s", start_dt)

                        # Sempre definir date_start e date_stop para MBWAY
                        update_vals['x_studio_date_start'] = start_dt
                        update_vals['x_studio_date_stop'] = start_dt + timedelta(minutes=constants.MBWAY_TIMEOUT_MINUTES)

                    if update_vals:
                        try:
                            self.with_context(from_api=True).write(update_vals)
                        except Exception:
                            pass
                    return self._notify(_('Pedido Criado com Sucesso!'), _('Pedido de pagamento %s criado.\nMétodo: %s') % (payment_method, self.x_name), 'success')

                except ValueError:
                    self._set_payment_failed("Resposta do servidor não é JSON válido")
                    return self._notify(_('❌ Erro de Resposta'), _('Resposta do servidor inválida'), 'danger')
            else:
                if response.status_code in [401, 403]:
                    try:
                        _logger.warning("[CRIAR PEDIDO] Erro de autenticação HTTP %s - marcando licença como cancelada", response.status_code)
                        config.sudo().write({'x_studio_status_check': 'canceled'})
                    except Exception as write_err:
                        _logger.error("[CRIAR PEDIDO] Erro ao atualizar x_studio_status_check: %s", str(write_err))
                else:
                    _logger.warning("[CRIAR PEDIDO] Erro HTTP %s - licença mantém-se ativa", response.status_code)

                error_message = "Erro desconhecido"
                try:
                    response_data = response.json()
                    if isinstance(response_data, dict):
                        error_message = (
                            response_data.get('message') or
                            response_data.get('error') or
                            response_data.get('detail') or
                            response_data.get('msg') or
                            str(response_data)
                        )
                    else:
                        error_message = str(response_data)

                except ValueError:
                    error_message = response.text[:300] if response.text else f"HTTP {response.status_code}"
                    _logger.error("[CRIAR PEDIDO] Erro na resposta (não JSON): %s", error_message)

                except Exception as parse_error:
                    error_message = f"Erro ao processar resposta: {str(parse_error)}"

                if response.status_code == 409:
                    try:
                        status_result = self.action_check_payment_status_from_integrator()

                        if status_result and status_result.get('status') == 'success':
                            return self._notify(_('✅ Transação Recuperada'), _('Esta transação já existia. Dados recuperados com sucesso.'), 'success')
                    except Exception as get_error:
                        _logger.error("[CRIAR PEDIDO] Erro ao tentar buscar dados existentes: %s", str(get_error))

                self._set_payment_failed(f"HTTP {response.status_code}: {error_message}")
                _logger.error("[CRIAR PEDIDO] Erro HTTP %s: %s", response.status_code, error_message)
                return self._notify('❌ Erro ao Criar Pagamento', _('Não foi possível concluir o pagamento. Por favor, tente novamente.'), 'danger')

        except requests.exceptions.Timeout as e:
            self._set_payment_failed(f"Timeout ao conectar ao integrador (30s): {str(e)}")
            return self._notify('❌ Timeout de Conexão', 'O integrador não respondeu em 30 segundos. Verifique a conexão.', 'danger')

        except requests.exceptions.ConnectionError as e:
            self._set_payment_failed(f"Erro de conexão ao integrador: {str(e)}")
            return self._notify('❌ Erro de Conexão', 'Não foi possível conectar ao integrador. Verifique o URL e a rede.', 'danger')

        except requests.exceptions.RequestException as e:
            self._set_payment_failed(f"Erro HTTP ao comunicar com integrador: {str(e)}")
            _logger.error("[CRIAR PEDIDO] Erro de comunicação: %s", str(e))
            return self._notify('❌ Erro de Comunicação', _('Não foi possível concluir o pagamento. Por favor, tente novamente.'), 'danger')

        except UserError:
            raise

        except Exception as e:
            self._set_payment_failed(f"Erro inesperado ao criar pedido: {str(e)}")
            _logger.error("[CRIAR PEDIDO] Erro inesperado: %s", str(e), exc_info=True)
            return self._notify('❌ Erro Inesperado', _('Não foi possível concluir o pagamento. Por favor, tente novamente.'), 'danger')
