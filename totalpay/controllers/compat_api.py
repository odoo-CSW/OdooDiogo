import odoo
import odoo.modules.registry
from odoo import http, api, SUPERUSER_ID
from odoo.http import request, Response
from odoo.service import db as db_service
import logging
import json
import werkzeug.wrappers
from passlib.hash import pbkdf2_sha512

_logger = logging.getLogger(__name__)

ALLOWED_MODELS = {
    "x_csw_totalpay",
}

FORBIDDEN_FIELDS = {
    "id",
    "create_uid",
    "create_date",
    "write_uid",
    "write_date",
    "__last_update",
}

class TotalPayCompatController(http.Controller):
    
    def __init__(self):
        super().__init__()

    def _extract_payload(self, kwargs):
        payload = kwargs or {}
        if not payload:
            jsonrequest = getattr(request, "jsonrequest", None)
            if isinstance(jsonrequest, dict):
                if isinstance(jsonrequest.get("params"), dict):
                    payload = jsonrequest.get("params")
                else:
                    payload = jsonrequest
        return payload or {}

    def _validate_api_key(self, cr, user_id, api_key):
        if not api_key:
            return False
        index = api_key[:8]
        cr.execute(
            """
            SELECT "key"
              FROM res_users_apikeys
             WHERE user_id = %s
               AND "index" = %s
               AND (expiration_date IS NULL OR expiration_date > NOW())
            """,
            (user_id, index),
        )
        rows = cr.fetchall()
        for (hashed_key,) in rows:
            try:
                if pbkdf2_sha512.verify(api_key, hashed_key):
                    return True
            except Exception as e:
                continue
        return False

    def _json_response(self, payload, status=200):
        return Response(
            json.dumps(payload),
            content_type='application/json',
            status=status,
        )

    def _log_update_result(self, ok, code=None, extra=None):
        if ok:
            _logger.info("[TOTALPAY API] Update OK%s", extra or "")
        else:
            _logger.warning("[TOTALPAY API] Update FAIL: %s", code or "unknown")

    def _error_response(self, status, code, message):
        self._log_update_result(False, code)
        return self._json_response({"ok": False, "error": message}, status=status)

    @http.route(
        ["/totalpay/api/v1/update"],
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def totalpay_update_http(self, db=None, **kwargs):
        try:
            # Ler o body do request
            data = request.httprequest.get_data(as_text=True)
            
            if not data:
                return self._error_response(400, "empty_body", "Invalid request")
            
            try:
                payload = json.loads(data)
                # Extrair params se for jsonrpc format
                if "params" in payload:
                    payload = payload["params"]
            except json.JSONDecodeError as e:
                return self._error_response(400, "invalid_json", "Invalid request")
            
            # Extrair dados
            target_db = payload.get("db")
            user_id_param = payload.get("user_id")
            api_key = payload.get("api_key")
            password = payload.get("password")  # Fallback para password se API key não funcionar
                        
            # Tentar obter lista de DBs (pode falhar com AccessDenied em Odoo.sh)
            available_dbs = []
            try:
                if hasattr(db_service, 'exp_list'):
                    available_dbs = db_service.exp_list()
            except Exception:
                # Sem acesso à lista - não é erro, seguimos em frente
                pass

            # Se não foi especificada a DB, tentar auto-detetar
            if not target_db:
                # Se só existe uma DB disponível, usar essa
                if len(available_dbs) == 1:
                    target_db = available_dbs[0]
                    _logger.info("[TOTALPAY API] Auto-selecionada DB única: %s", target_db)
                else:
                    # Tentar deduzir do header X-Database ou host
                    header_db = None
                    try:
                        header_db = request.httprequest.headers.get('X-Database')
                    except Exception:
                        pass
                    
                    host_db = None
                    try:
                        host = request.httprequest.environ.get('HTTP_HOST', '').split(':')[0]
                        if host and '.' in host:
                            host_db = host.split('.')[0]
                    except Exception:
                        pass
                    
                    # Testar candidatos
                    for cand in [header_db, host_db]:
                        if cand:
                            try:
                                test_reg = odoo.modules.registry.Registry(cand)
                                test_cr = test_reg.cursor()
                                test_cr.close()
                                target_db = cand
                                _logger.info("[TOTALPAY API] DB deduzida: %s", target_db)
                                break
                            except Exception:
                                continue
            
            # Se ainda não temos DB, erro
            if not target_db:
                return self._error_response(400, "missing_db", "Database not specified; provide 'db' in payload or X-Database header")
            
            # Tentar conectar à DB (aqui é que importa se tem ou não permissões)
            try:
                reg = odoo.modules.registry.Registry(target_db)
                cr = reg.cursor()
            except Exception as e:
                _logger.error("[TOTALPAY API] Não foi possível aceder à DB '%s': %s", target_db, e)
                return self._error_response(500, "db_access_failed", "Cannot access database")
            
            try:
                env = api.Environment(cr, SUPERUSER_ID, {})
                
                # Buscar utilizador por ID
                if not user_id_param:
                    return self._error_response(400, "missing_user_id", "Invalid request")
                
                user = env['res.users'].browse(int(user_id_param))
                if not user.exists():
                    cr.close()
                    return self._error_response(404, "user_not_found", "Resource not found")
                                
                # Validar credenciais - tentar API key primeiro, depois password
                authenticated = False
                auth_method = None
                
                # Tentar autenticação por API key
                if api_key:
                    if self._validate_api_key(cr, user.id, api_key):
                        authenticated = True
                        auth_method = "API Key"
                
    
                if not authenticated:
                    cr.close()
                    _logger.warning("[TOTALPAY API] Falha de autenticação para user_id=%s (tentou: api_key=%s, password=%s)", 
                                  user_id_param, 'sim' if api_key else 'não', 'sim' if password else 'não')
                    return self._error_response(401, "auth_failed", "Unauthorized")
                
                _logger.info("[TOTALPAY API] ✓ Autenticado: user=%s, método=%s", user.login, auth_method)
                user_id = user.id
                
                # Criar env com user autenticado
                # Contexto para desativar emails, tracking e automações
                context = {
                    'tracking_disable': True,        # Desativa tracking de mudanças
                    'mail_create_nolog': True,       # Não cria mensagens no chatter
                    'mail_notrack': True,            # Não faz tracking de emails
                    'mail_auto_subscribe_no_notify': True,  # Não notifica subscritores
                }
                env = api.Environment(cr, user_id, context)
                
                # Obter model e domain
                model_name = payload.get("model") or "x_csw_totalpay"
                if model_name not in ALLOWED_MODELS:
                    return self._error_response(403, "model_not_allowed", "Forbidden")
                
                domain = payload.get("domain")
                if not domain:
                    x_name_value = payload.get("x_name")
                    if not x_name_value:
                        return self._error_response(400, "missing_domain", "Invalid request")
                    domain = [("x_name", "=", x_name_value)]
                
                if not isinstance(domain, list):
                    return self._error_response(400, "invalid_domain", "Invalid request")
                
                # Buscar registos
                Model = env[model_name]
                records = Model.search(domain)
                
                if not records:
                    return self._error_response(404, "record_not_found", "Resource not found")
                
                # Validar values
                values = payload.get("values")
                if not isinstance(values, dict) or not values:
                    return self._error_response(400, "invalid_values", "Invalid request")
                
                unknown_fields = [f for f in values if f not in Model._fields]
                if unknown_fields:
                    return self._error_response(400, "unknown_fields", "Invalid request")
                
                invalid_fields = [f for f in values if f in FORBIDDEN_FIELDS]
                if invalid_fields:
                    return self._error_response(403, "forbidden_fields", "Forbidden")
                
                # Atualizar registos
                records.write(values)
                cr.commit()
                self._log_update_result(True, extra=f" db={cr.dbname} model={model_name} updated={len(records)}")
                return self._json_response(
                    {
                        "ok": True,
                        "database": cr.dbname,
                        "model": model_name,
                        "domain": domain,
                        "x_name": payload.get("x_name"),
                        "updated_count": len(records),
                        "updated_ids": records.ids,
                    },
                    status=200,
                )
                
            finally:
                cr.close()
                
        except Exception as e:
            _logger.exception("[TOTALPAY API] Internal error processing update: %s", e)
            return self._error_response(500, "internal_error", "Internal error")