from fastapi import APIRouter, Depends, HTTPException
from core.auth import get_session_user
from schemas.chat import CreateCategoryRequest, UpdateCategoryRequest
from repositories.categories import list_categories, create_category, update_category, delete_category

router = APIRouter()

@router.get("/api/chat/categories")
async def list_categories_route(session: dict = Depends(get_session_user)):
    return list_categories()

@router.post("/api/chat/categories")
async def create_category_route(req: CreateCategoryRequest, session: dict = Depends(get_session_user)):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Name required")
    try:
        return create_category(name)
    except Exception:
        raise HTTPException(409, "Category already exists")

@router.patch("/api/chat/categories/{category_id}")
async def update_category_route(category_id: int, req: UpdateCategoryRequest, session: dict = Depends(get_session_user)):
    cat = update_category(category_id, {"name": req.name, "is_collapsed": req.is_collapsed})
    if not cat:
        raise HTTPException(404, "Category not found")
    return cat

@router.delete("/api/chat/categories/{category_id}")
async def delete_category_route(category_id: int, session: dict = Depends(get_session_user)):
    delete_category(category_id)
    return {"ok": True}
