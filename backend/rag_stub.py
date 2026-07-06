"""RAG 桩模块 — 预留，当前全部返回空/501"""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/documents")
async def list_documents():
    """返回空文档列表"""
    return {"documents": []}


@router.post("/documents/upload/async")
async def upload_document():
    """RAG 未启用"""
    raise HTTPException(status_code=501, detail="RAG 功能尚未启用")


@router.get("/documents/upload/jobs/{job_id}")
async def get_upload_job(job_id: str):
    """上传任务不存在"""
    raise HTTPException(status_code=404, detail="上传任务不存在")


@router.delete("/documents/delete/async/{filename}")
async def delete_document(filename: str):
    """RAG 未启用"""
    raise HTTPException(status_code=501, detail="RAG 功能尚未启用")


@router.get("/documents/delete/jobs/{job_id}")
async def get_delete_job(job_id: str):
    """删除任务不存在"""
    raise HTTPException(status_code=404, detail="删除任务不存在")
