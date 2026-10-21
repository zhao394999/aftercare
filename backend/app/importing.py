import csv
import io
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .schemas import OrderInput
from .security import require
from .domain import utc

ALIASES = {
    "订单号": "external_id",
    "商品名称": "product",
    "数量": "quantity",
    "实付金额": "paid_amount",
    "订单状态": "status",
    "下单时间": "placed_at",
    "发货时间": "shipped_at",
    "承诺送达时间": "promised_at",
    "历史已退金额": "refunded_amount",
    "客户": "customer",
    "渠道": "channel",
    "快递公司编码": "carrier_code",
    "运单号": "tracking_number",
    "查件校验号码": "tracking_phone",
}
STATUS = {
    "已付款": "paid",
    "已发货": "shipped",
    "已签收": "delivered",
    "已取消": "cancelled",
    "已退款": "refunded",
}


def money_cents(value: str, allow_zero=False) -> int:
    try:
        number = Decimal(value)
        require(
            number.is_finite()
            and (number >= 0 if allow_zero else number > 0)
            and number == number.quantize(Decimal(".01")),
            "invalid_money",
            "金额必须为正数且最多两位小数",
            422,
        )
        return int(number * 100)
    except InvalidOperation:
        require(False, "invalid_money", "金额格式不正确", 422)


def parse_csv(content: bytes, mapping: str = "{}"):
    require(
        0 < len(content) <= 10 * 1024 * 1024,
        "import_size",
        "CSV文件大小须在10MB以内",
        422,
    )
    try:
        source = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        require(False, "import_encoding", "请将CSV保存为UTF-8编码后重试", 422)
    try:
        custom = json.loads(mapping)
        require(
            isinstance(custom, dict)
            and len(custom) <= 20
            and all(
                isinstance(k, str) and isinstance(v, str) for k, v in custom.items()
            ),
            "import_mapping",
            "字段映射格式不正确",
            422,
        )
    except ValueError:
        require(False, "import_mapping", "字段映射须为JSON对象", 422)
    reader = csv.DictReader(io.StringIO(source))
    try:
        fields = reader.fieldnames
    except csv.Error:
        require(False, "import_csv", "CSV格式无效或单个字段过长，请检查文件", 422)
    require(fields is not None, "import_empty", "CSV缺少表头", 422)
    mapped = {key: custom.get(key, ALIASES.get(key, key)) for key in fields}
    require(
        len(set(mapped.values())) == len(mapped),
        "import_duplicate_columns",
        "多个列映射到了相同字段",
        422,
    )
    required = {"external_id", "product", "quantity", "status", "placed_at"}
    require(
        required <= set(mapped.values())
        and ({"paid_cents", "paid_amount"} & set(mapped.values())),
        "import_columns",
        "缺少订单号、商品、数量、金额、状态或下单时间；请使用模板或配置映射",
        422,
    )
    rows, errors, seen = [], [], set()
    allowed = set(OrderInput.model_fields)
    for line in range(2, 10003):
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error:
            require(False, "import_csv", "CSV格式无效或单个字段过长，请检查文件", 422)
        require(line <= 10001, "import_rows_limit", "单次最多导入10000行", 422)
        try:
            require(None not in row, "row_columns", "该行列数超出表头", 422)
            clean = {
                mapped[k]: v.strip()
                for k, v in row.items()
                if k in mapped and v is not None and v.strip()
            }
            if "paid_amount" in clean:
                clean["paid_cents"] = money_cents(clean.pop("paid_amount"))
            if "refunded_amount" in clean:
                clean["refunded_cents"] = money_cents(
                    clean.pop("refunded_amount"), True
                )
            clean["status"] = STATUS.get(clean.get("status"), clean.get("status"))
            for field in ["placed_at", "shipped_at", "promised_at"]:
                if clean.get(field):
                    try:
                        value = datetime.fromisoformat(
                            clean[field].replace("Z", "+00:00")
                        )
                    except ValueError:
                        require(False, "import_time", "日期格式不正确", 422)
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                    clean[field] = utc(value).isoformat() + "Z"
            payload = OrderInput.model_validate(
                {k: v for k, v in clean.items() if k in allowed}
            )
            key = (payload.channel, payload.external_id)
            require(key not in seen, "duplicate_row", "文件内订单重复", 422)
            seen.add(key)
            rows.append(payload.model_dump(mode="json"))
        except ValidationError as exc:
            errors.append(
                {
                    "line": line,
                    "message": "字段格式不符合要求",
                    "fields": [
                        str(e["loc"][0]) if e["loc"] else "order" for e in exc.errors()
                    ],
                }
            )
        except Exception as exc:
            if hasattr(exc, "code"):
                errors.append({"line": line, "message": exc.message, "fields": []})
            else:
                raise
    return rows, errors
