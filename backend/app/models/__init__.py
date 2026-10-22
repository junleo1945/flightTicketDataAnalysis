# 导入所有模型，确保 Alembic 能发现完整的表结构
from app.models.analysis import CommissionRule, DataQualityStat, DataQualitySync, QueryStat  # noqa: F401
from app.models.order import OrderAirline, OrderRoute, SupplierOrder, TmOrder  # noqa: F401
from app.models.upload import RawOrderRow, UploadBatch  # noqa: F401
from app.models.chat import ChatLog, Conversation  # noqa: F401
from app.models.auth import AuthSession, User  # noqa: F401
from app.models.grant import DataGrant  # noqa: F401
