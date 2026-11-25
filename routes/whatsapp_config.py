"""
WhatsApp connection settings router (Settings → WhatsApp Connection).

  GET    /whatsapp/config                      — saved settings (secrets masked), status, webhook URL
  PUT    /whatsapp/config                      — save the credentials
  POST   /whatsapp/config/test                 — ask Meta whether the credentials work
  POST   /whatsapp/config/subscribe            — subscribe the Meta app to this WABA's webhook events
  POST   /whatsapp/config/rotate-verify-token  — generate a new webhook verify token
  DELETE /whatsapp/config                      — disconnect and delete the stored credentials

Access uses the existing "integrations" permission (the same one as API keys): read to view,
update to change anything.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from auth import get_tenant_db
from config import PUBLIC_API_URL
from models.employee import Employee
from schemas.whatsapp_config import WhatsAppConfigResponse, WhatsAppConfigUpdate, WhatsAppTestResult
from services import whatsapp_config_service as svc
from services.permissions import require_permission

router = APIRouter(prefix="/whatsapp/config", tags=["whatsapp-config"])

view_config = require_permission("integrations", "r")
manage_config = require_permission("integrations", "u")


def _webhook_url(request: Request) -> str:
    base = PUBLIC_API_URL or str(request.base_url)
    return f"{base.rstrip('/')}/webhooks/whatsapp"


@router.get("", response_model=WhatsAppConfigResponse, summary="Get WhatsApp connection settings")
def get_config(request: Request, db: Session = Depends(get_tenant_db), _: Employee = Depends(view_config)):
    return svc.to_response(svc.get_config(db), _webhook_url(request))


@router.put("", response_model=WhatsAppConfigResponse, summary="Save WhatsApp connection settings")
def save_config(
    body: WhatsAppConfigUpdate,
    request: Request,
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(manage_config),
):
    cfg = svc.save_config(db, current_user._tenant_schema, body, current_user.id)
    return svc.to_response(cfg, _webhook_url(request))


@router.post("/test", response_model=WhatsAppTestResult, summary="Test the WhatsApp connection")
def test_config(request: Request, db: Session = Depends(get_tenant_db), _: Employee = Depends(manage_config)):
    ok, detail, cfg = svc.test_connection(db)
    return WhatsAppTestResult(ok=ok, status=cfg.status, detail=detail, config=svc.to_response(cfg, _webhook_url(request)))


@router.post("/subscribe", summary="Subscribe the app to this WhatsApp Business Account")
def subscribe(db: Session = Depends(get_tenant_db), _: Employee = Depends(manage_config)):
    ok, detail = svc.subscribe_app(db)
    if not ok:
        raise HTTPException(status_code=502, detail=detail)
    return {"ok": True, "detail": detail}


@router.post("/rotate-verify-token", response_model=WhatsAppConfigResponse, summary="Generate a new verify token")
def rotate_verify_token(request: Request, db: Session = Depends(get_tenant_db), _: Employee = Depends(manage_config)):
    return svc.to_response(svc.rotate_verify_token(db), _webhook_url(request))


@router.delete("", status_code=204, summary="Disconnect WhatsApp and delete the stored credentials")
def delete_config(
    db: Session = Depends(get_tenant_db),
    current_user: Employee = Depends(manage_config),
):
    svc.delete_config(db, current_user._tenant_schema)
