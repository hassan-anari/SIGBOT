from chainura.domain.signalist import Signalist
from chainura.persistence.models import SignalistRecord


def to_domain(record: SignalistRecord) -> Signalist:
    return Signalist.rehydrate(
        id=record.id,
        telegram_user_id=record.telegram_user_id,
        display_name=record.display_name,
        telegram_username=record.telegram_username,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        archived_at=record.archived_at,
    )
