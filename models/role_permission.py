from sqlalchemy import String, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class RolePermission(Base):
    """An admin or manager's override of one action for one role (Settings > Roles & Permissions).

    A missing row means the default in role_access.ACTION_ROLES applies.
    """

    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_slug", "action", name="uq_role_permission"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_slug: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(40))
    allowed: Mapped[int] = mapped_column(SmallInteger, default=0)
