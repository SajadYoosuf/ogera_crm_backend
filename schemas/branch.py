from pydantic import BaseModel, ConfigDict


class BranchResponse(BaseModel):
    """
    Branch returned in contact responses and the GET /branches list.

    id   — use this when creating/updating a contact (send branch_id=1)
    code — short slug for the branch (e.g. 'malappuram')
    name — human-readable label for display (e.g. 'Malappuram')
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    is_active: bool
