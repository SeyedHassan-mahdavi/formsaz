"""Add platform field to ReportItem

Revision ID: ad8e70746a18
Revises: b4f53432bf07
Create Date: 2025-08-23 13:36:25.083741

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ad8e70746a18'
down_revision: Union[str, Sequence[str], None] = 'b4f53432bf07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('report_items', sa.Column('platform', sa.String(64), nullable=True))

def downgrade():
    op.drop_column('report_items', 'platform')