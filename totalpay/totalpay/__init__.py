import logging
import json
from odoo import http
from odoo.service import db as db_service

_logger = logging.getLogger(__name__)

# Patch para permitir rotas /totalpay/api/* sem dbfilter configurado
# Isto permite que o módulo funcione em on-premise e Odoo.sh
_original_db_filter = http.db_filter
_original_db_list = http.db_list

def _totalpay_db_filter(dbs, host=None, **kwargs):
    """
    Permite bypass de dbfilter para rotas /totalpay/api/*
    Tenta selecionar a DB pedida no payload para construir o routing map
    O código da API depois conecta à DB correta especificada no payload
    """
    # Tentar obter o request do contexto
    try:
        from odoo.http import request as current_request
        if current_request and hasattr(current_request, 'httprequest'):
            path = current_request.httprequest.path
            if path and path.startswith('/totalpay/api/'):
                # Tentar obter DB do payload para escolher a DB correta no routing
                target_db = None
                try:
                    data = current_request.httprequest.get_data(as_text=True)
                    if data:
                        payload = json.loads(data)
                        if isinstance(payload, dict):
                            if isinstance(payload.get("params"), dict):
                                payload = payload["params"]
                            target_db = payload.get("db") or payload.get("dbfilter")
                except Exception:
                    target_db = None

                if target_db and target_db in dbs:
                    return [target_db]

                # Fallback: usar a primeira DB para evitar 404 no routing
                if dbs:
                    selected_db = [dbs[0]]
                    return selected_db
                return dbs
    except:
        pass
    
    # Para outras rotas, usar o comportamento original
    return _original_db_filter(dbs, host=host, **kwargs)

def _totalpay_db_list(force=False, host=None, **kwargs):
    """
    Wrapper para db_list que suporta bypass para rotas TotalPay
    Aceita host como argumento (assinatura original do Odoo)
    """
    # Tentar obter o request do contexto
    try:
        from odoo.http import request as current_request
        if current_request and hasattr(current_request, 'httprequest'):
            path = current_request.httprequest.path
            if path and path.startswith('/totalpay/api/'):
                dbs = _original_db_list(force=force, host=host, **kwargs)
                return dbs
    except:
        pass
    
    return _original_db_list(force=force, host=host, **kwargs)

# Aplicar patches
http.db_filter = _totalpay_db_filter
http.db_list = _totalpay_db_list

from . import controllers
from . import models
from .models.account_payment_register import post_init_hook
