from fastapi import APIRouter, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

from app.telegram.telegram_service import get_channel_information
from app.database.models import upsert_channel, get_all_channels

templates = Jinja2Templates(
    directory="app/dashboard/templates"
)

router = APIRouter()


@router.get("/dashboard")
def dashboard(
    request: Request,
    success: int = 0,
    error: int = 0,
):
    channels = get_all_channels()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "channels": channels,
            "success": success,
            "error": error, 
        }
    )

@router.post("/dashboard/add-channel")
async def add_channel_from_dashboard(
    username: str = Form(...)
):

    try:

        telegram_channel = await get_channel_information(
            username
        )

        upsert_channel(
            channel_id=telegram_channel["channel_id"],
            channel_title=telegram_channel["channel_title"],
            channel_username=telegram_channel["channel_username"],
        )

    except Exception:

        return RedirectResponse(
            url="/dashboard?error=1",
            status_code=303,
        )

    return RedirectResponse(
        url="/dashboard?success=1",
        status_code=303,
    )