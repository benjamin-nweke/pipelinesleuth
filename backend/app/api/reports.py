"""
Reports API router for viewing and downloading reports
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.database.session import get_db
from backend.app.models.report import InvestigationReport

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{report_id}/download/markdown")
async def download_markdown_report(report_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(InvestigationReport).where(InvestigationReport.id == report_id)
    )
    report = res.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return Response(
        content=report.markdown_content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f"attachment; filename=pipelinesleuth_report_{report.id}.md"
        }
    )


@router.get("/{report_id}/download/html")
async def download_html_report(report_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(InvestigationReport).where(InvestigationReport.id == report_id)
    )
    report = res.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Simple clean HTML rendering
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{report.title} - PipelineSleuth Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.6; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1f2937; }}
    h1 {{ color: #111827; border-bottom: 2px solid #f59e0b; padding-bottom: 8px; }}
    h3 {{ color: #374151; margin-top: 24px; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 0.9em; }}
    pre {{ background: #111418; color: #f0f6fc; padding: 16px; border-radius: 8px; overflow-x: auto; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 0.85em; font-weight: 600; background: #fef3c7; color: #92400e; }}
  </style>
</head>
<body>
  <div class="badge">PipelineSleuth Forensic Diagnosis</div>
  <pre>{report.markdown_content}</pre>
</body>
</html>"""

    return Response(
        content=html,
        media_type="text/html",
        headers={
            "Content-Disposition": f"attachment; filename=pipelinesleuth_report_{report.id}.html"
        }
    )
