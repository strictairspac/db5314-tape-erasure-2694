"""结档（匿名归档关闭）的领域规则与脱敏逻辑。

结档时仅移除业务档案中可回溯到个人或源对象的字段：
素材对象路径 ``object_key`` 与演员编号 ``performer_id``。
资产编号、引用关系、合同关联、到期日与时长等仍用于留存核对的字段保持不变。
"""

from __future__ import annotations

import copy

SCRUBBED_ASSET_FIELDS = ('object_key', 'performer_id')


def scrub_asset(asset):
    """返回单条资产的脱敏副本，源路径与演员编号不再保留。"""
    clean = dict(asset)
    for field in SCRUBBED_ASSET_FIELDS:
        if field in clean:
            clean[field] = None
    return clean


def scrub_payload(payload):
    """返回整份档案输入的脱敏副本，不修改入参。"""
    clean = copy.deepcopy(payload)
    assets = clean.get('assets')
    if isinstance(assets, list):
        clean['assets'] = [scrub_asset(row) if isinstance(row, dict) else row for row in assets]
    return clean


def is_closed(case_row):
    return bool(case_row and case_row.get('closed_at'))
