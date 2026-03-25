# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re
import logging
from . import constants

_logger = logging.getLogger(__name__)


class PaymentMethodMixin(models.AbstractModel):
    """Mixin para campos e validações de métodos de pagamento MB WAY e PayPal"""
    _name = 'payment.method.mixin'
    _description = 'Payment Method Mixin'

    x_mbway_phone = fields.Char(
        string='Número MB WAY',
        help='Número de telefone associado ao MB WAY'
    )

    x_paypal_email = fields.Char(
        string='E-mail PayPal',
        help='Endereço de e-mail associado ao PayPal'
    )
    
    payment_method_code = fields.Char(
        string='Payment Method Code',
        compute='_compute_payment_method_code',
    )
    
    @api.depends('payment_method_line_id.payment_method_id.code')
    def _compute_payment_method_code(self):
        for record in self:
            method = record.payment_method_line_id.payment_method_id
            record.payment_method_code = method.code.upper() if method and method.code else False

    @api.onchange('partner_id', 'payment_method_line_id')
    def _onchange_partner_payment_method(self):
        """Preenche automaticamente telefone MB WAY e email PayPal do parceiro"""
        if self.partner_id and self.payment_method_line_id:
            method_code = (self.payment_method_code or '').upper()

            if method_code == constants.PAYMENT_METHOD_MBWAY:
                # Buscar telemóvel do parceiro (prioriza mobile, depois phone)
                partner_mobile = getattr(self.partner_id, 'mobile', False)
                partner_phone = getattr(self.partner_id, 'phone', False)
                raw_phone = partner_mobile or partner_phone
                
                if raw_phone:
                    # Limpar e normalizar o número
                    digits = re.sub(r'\D', '', raw_phone)
                    # Remover prefixos internacionais de Portugal
                    if digits.startswith('00351') and len(digits) > 9:
                        digits = digits[-9:]
                    elif digits.startswith('351') and len(digits) > 9:
                        digits = digits[-9:]
                    
                    self.x_mbway_phone = digits
                    _logger.info("[MIXIN AUTO-FILL] Telemóvel MB WAY preenchido: %s (de %s)", digits, raw_phone)
                else:
                    _logger.warning("[MIXIN AUTO-FILL] Parceiro %s não tem telemóvel", self.partner_id.name)

            elif method_code == 'PAYPAL' and not self.x_paypal_email:
                self.x_paypal_email = self.partner_id.email


    @api.constrains('x_mbway_phone', 'payment_method_line_id')
    def _check_mbway_phone(self):
        """Valida formato do número de telefone MB WAY"""
        for record in self:
            method = record.payment_method_line_id.payment_method_id
            if not record.x_mbway_phone or not method or method.code.upper() != constants.PAYMENT_METHOD_MBWAY:
                continue

            phone = re.sub(r'[^\d]', '', record.x_mbway_phone)
            if phone.startswith('00351') and len(phone) > 9:
                phone = phone[-9:]
            elif phone.startswith('351') and len(phone) > 9:
                phone = phone[-9:]

            if not re.match(r'^9\d{8}$', phone):
                raise ValidationError(
                    _('Número MB WAY inválido!\n'
                      'Deve ter 9 dígitos e começar com 9.\n'
                      'Exemplo: 912345678, 912 345 678 ou +351 912 345 678')
                )

    @api.constrains('x_paypal_email', 'payment_method_line_id')
    def _check_paypal_email(self):
        """Valida formato do email PayPal"""
        for record in self:
            method = record.payment_method_line_id.payment_method_id
            if not record.x_paypal_email or not method or method.code.upper() != 'PAYPAL':
                continue

            if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', record.x_paypal_email.strip()):
                raise ValidationError(
                    _('E-mail PayPal inválido!\n'
                      'Por favor, insira um endereço de e-mail válido.\n'
                      'Exemplo: cliente@exemplo.com')
                )
