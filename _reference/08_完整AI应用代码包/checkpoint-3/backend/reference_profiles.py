"""Source-labelled classroom inputs derived from one feature run's drawing facts.

The catalog is deliberately local and versioned so a classroom run does not
depend on network availability. Public sources explain cost structure, market
trend, and applicable standards; the numeric rates remain explicit classroom
assumptions and never represent a supplier quote or a released process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    ClassroomReferenceProfile,
    DrawingFactsV1,
    ManufacturingFamily,
    PreQuoteRequest,
    ReferenceParameterBasis,
    ReferenceSource,
)


ACCESSED_AT = "2026-08-10"


@dataclass(frozen=True)
class _ProfileDefaults:
    quantity: int
    material_form: str
    equipment_capability: str
    inspection_capability: str
    special_requirements: str
    quote_inputs: dict[str, float | str]


_DEFAULTS: dict[ManufacturingFamily, _ProfileDefaults] = {
    ManufacturingFamily.CNC_MACHINING: _ProfileDefaults(
        quantity=100,
        material_form="6061 铝合金板/棒料（课堂参考；实际牌号与毛坯待确认）",
        equipment_capability="三轴立式加工中心、常用车铣钻刀具和通用夹具；具体行程、精度和可达性待现场确认",
        inspection_capability="卡尺、千分尺、高度仪和三坐标；关键特性按首件全检课堂场景设定",
        special_requirements="关键特性首件全检；刀路、切削参数、夹具和量检具在试制前确认",
        quote_inputs={
            "net_weight_kg": 0.5,
            "material_unit_price": 25,
            "material_loss_rate_pct": 15,
            "setup_hours": 2,
            "processing_minutes_per_part": 12,
            "machine_hourly_rate": 150,
            "tooling_cost": 500,
            "outsourcing_cost": 0,
            "inspection_packaging_per_part": 5,
            "logistics_cost": 200,
            "overhead_rate_pct": 12,
            "risk_rate_pct": 8,
            "target_margin_pct": 20,
            "currency": "CNY",
        },
    ),
    ManufacturingFamily.SHEET_METAL: _ProfileDefaults(
        quantity=100,
        material_form="冷轧薄钢板（课堂参考；实际牌号、板厚与表面状态待确认）",
        equipment_capability="激光切割/数冲、去毛刺、折弯机和通用模具；吨位、模口与回弹补偿待现场确认",
        inspection_capability="卡尺、高度尺、角度尺和简易检具；关键空间尺寸首件全检",
        special_requirements="确认板厚、纹理方向、外观面与折弯回弹；首件合格后再批量",
        quote_inputs={
            "net_weight_kg": 0.2,
            "material_unit_price": 8,
            "material_loss_rate_pct": 15,
            "setup_hours": 1.5,
            "processing_minutes_per_part": 6,
            "machine_hourly_rate": 120,
            "tooling_cost": 300,
            "outsourcing_cost": 200,
            "inspection_packaging_per_part": 3,
            "logistics_cost": 150,
            "overhead_rate_pct": 10,
            "risk_rate_pct": 8,
            "target_margin_pct": 20,
            "currency": "CNY",
        },
    ),
    ManufacturingFamily.INJECTION_MOLDING: _ProfileDefaults(
        quantity=1000,
        material_form="ABS/PP 通用注塑粒料（课堂参考；实际牌号、颜色与回料规则待确认）",
        equipment_capability="通用注塑机、已存在模具、干燥/混料/取件设施；锁模力、射胶能力和工艺窗口待确认",
        inspection_capability="卡尺、外观限度样件、称重与功能检查；稳定周期后再进入抽检",
        special_requirements="本课堂场景假设已有可用模具；材料干燥、收缩率与成型窗口待试模确认",
        quote_inputs={
            "net_weight_kg": 0.12,
            "material_unit_price": 22,
            "material_loss_rate_pct": 5,
            "setup_hours": 3,
            "processing_minutes_per_part": 0.75,
            "machine_hourly_rate": 180,
            "tooling_cost": 1000,
            "outsourcing_cost": 0,
            "inspection_packaging_per_part": 1.5,
            "logistics_cost": 200,
            "overhead_rate_pct": 10,
            "risk_rate_pct": 8,
            "target_margin_pct": 20,
            "currency": "CNY",
        },
    ),
    ManufacturingFamily.ASSEMBLY: _ProfileDefaults(
        quantity=100,
        material_form="标准件与外购件组合（课堂参考；BOM 、替代件与齐套成本待确认）",
        equipment_capability="装配工位、通用定位工装、扭矩工具、防错与扫码设施",
        inspection_capability="卡尺、扭矩记录、功能测试和外观检查；关键连接点设停检点",
        special_requirements="确认 BOM 版本、齐套状态、防错、关键扭矩和功能判定标准",
        quote_inputs={
            "net_weight_kg": 1,
            "material_unit_price": 15,
            "material_loss_rate_pct": 3,
            "setup_hours": 1,
            "processing_minutes_per_part": 8,
            "machine_hourly_rate": 80,
            "tooling_cost": 200,
            "outsourcing_cost": 0,
            "inspection_packaging_per_part": 3,
            "logistics_cost": 150,
            "overhead_rate_pct": 10,
            "risk_rate_pct": 5,
            "target_margin_pct": 20,
            "currency": "CNY",
        },
    ),
}


def _source_catalog(family: ManufacturingFamily) -> list[ReferenceSource]:
    sources = [
        ReferenceSource(
            id="nbs-ppi-2026-06",
            title="2026 年 6 月份工业生产者出厂价格",
            publisher="中华人民共和国国家统计局",
            url="https://www.stats.gov.cn/sj/zxfb/202607/t20260709_1964083.html",
            accessed_at=ACCESSED_AT,
            role="材料与工业品价格趋势背景",
            note="PPI 只用于趋势校准，不是具体材料或供应商报价。",
        ),
        ReferenceSource(
            id="nist-cost-guide",
            title="Manufacturing Cost Guide: A Primer",
            publisher="U.S. National Institute of Standards and Technology",
            url="https://www.nist.gov/publications/manufacturing-cost-guide-primer-beta-version-01",
            accessed_at=ACCESSED_AT,
            role="制造成本分解方法",
            note="用于解释材料、加工、工装、管理与风险等成本结构，不提供本地企业费率。",
        ),
    ]
    if family in {ManufacturingFamily.CNC_MACHINING, ManufacturingFamily.SHEET_METAL}:
        sources.append(
            ReferenceSource(
                id="shfe-daily-market",
                title="Daily Market Data",
                publisher="Shanghai Futures Exchange",
                url="https://www.shfe.com.cn/eng/reports/StatisticalData/DailyData/",
                accessed_at=ACCESSED_AT,
                role="铜、铝、钢材等金属市场趋势背景",
                note="期货市场数据仅做方向性参考，未计入规格、加工、运输、税费与供应商加价。",
            )
        )

    standard_by_family = {
        ManufacturingFamily.CNC_MACHINING: ReferenceSource(
            id="iso-286-2",
            title="ISO 286-2:2010 几何产品技术规范—线性尺寸公差表",
            publisher="International Organization for Standardization",
            url="https://www.iso.org/standard/54915.html",
            accessed_at=ACCESSED_AT,
            role="尺寸公差与配合的标准索引",
            note="标准仅用于识别需确认的工程依据，不自动补写图纸公差。",
        ),
        ManufacturingFamily.SHEET_METAL: ReferenceSource(
            id="iso-13920",
            title="ISO 13920:2023 焊接结构一般公差",
            publisher="International Organization for Standardization",
            url="https://www.iso.org/cms/live/live/en/sites/isoorg/contents/data/standard/08/60/86032.html",
            accessed_at=ACCESSED_AT,
            role="钣金/焊接结构尺寸与几何公差索引",
            note="只在图纸或技术协议适用时使用；不自动代替产品特定要求。",
        ),
        ManufacturingFamily.INJECTION_MOLDING: ReferenceSource(
            id="iso-20457",
            title="ISO 20457:2025 Plastics moulded parts — Tolerances and acceptance conditions",
            publisher="International Organization for Standardization",
            url="https://www.iso.org/standard/90304.html",
            accessed_at=ACCESSED_AT,
            role="注塑件公差、验收与模具条件索引",
            note="用于提醒收缩、模具和验收条件；具体数值仍必须来自受控要求。",
        ),
        ManufacturingFamily.ASSEMBLY: ReferenceSource(
            id="iso-286-2",
            title="ISO 286-2:2010 几何产品技术规范—线性尺寸公差表",
            publisher="International Organization for Standardization",
            url="https://www.iso.org/standard/54915.html",
            accessed_at=ACCESSED_AT,
            role="装配接口与配合的标准索引",
            note="标准只用于提醒核对接口和配合，不取代 BOM、总成图或功能规范。",
        ),
    }
    sources.append(standard_by_family[family])
    return sources


def _infer_family(facts: DrawingFactsV1) -> tuple[ManufacturingFamily, float, list[str]]:
    values = {item.name: item.value.strip() for item in facts.facts}
    searchable = " ".join(value for value in values.values() if value).lower()
    scores = {family: 0 for family in ManufacturingFamily}
    reasons: dict[ManufacturingFamily, list[str]] = {family: [] for family in ManufacturingFamily}

    if facts.document_type.value == "assembly_drawing":
        scores[ManufacturingFamily.ASSEMBLY] += 6
        reasons[ManufacturingFamily.ASSEMBLY].append("文档类型被 AI 识别为总成图")

    keyword_groups = {
        ManufacturingFamily.SHEET_METAL: ("板厚", "钣金", "折弯", "冲压", "冲裁", "薄板", "sheet metal"),
        ManufacturingFamily.INJECTION_MOLDING: ("注塑", "模塑", "树脂", "塑料", "脱模", "abs", "polypropylene"),
        ManufacturingFamily.ASSEMBLY: ("总成", "装配", "bom", "爆炸图", "assembly"),
        ManufacturingFamily.CNC_MACHINING: ("机加", "机械加工", "铣", "车削", "钻孔", "h7", "加工中心", "cnc"),
    }
    for family, keywords in keyword_groups.items():
        matches = [keyword for keyword in keywords if keyword in searchable]
        if matches:
            scores[family] += len(matches) * 2
            reasons[family].append(f"已确认事实中出现：{' / '.join(matches[:3])}")

    material = values.get("material", "").lower()
    if material and re.search(r"(?:^|[^a-z])(pp|pa|pc|pom|pbt)(?:[^a-z]|$)|abs|塑料|树脂", material):
        scores[ManufacturingFamily.INJECTION_MOLDING] += 3
        reasons[ManufacturingFamily.INJECTION_MOLDING].append("材料信息符合塑料成型场景")

    best = max(scores, key=scores.get)
    best_score = scores[best]
    if best_score == 0:
        return (
            ManufacturingFamily.CNC_MACHINING,
            0.58,
            ["图纸未出现明确成型/装配信号，按通用机加工课堂场景起步"],
        )
    confidence = 0.92 if best_score >= 6 else 0.82 if best_score >= 3 else 0.72
    return best, confidence, reasons[best]


def _material_form(facts: DrawingFactsV1, family: ManufacturingFamily, fallback: str) -> str:
    material = next((item.value.strip() for item in facts.facts if item.name == "material"), "")
    if not material or material.lower() in {"未识别", "待确认", "unknown", "n/a", "none"}:
        return fallback
    suffixes = {
        ManufacturingFamily.CNC_MACHINING: "；毛坯按板/棒料课堂场景预填，待现场确认",
        ManufacturingFamily.SHEET_METAL: "；板材/卷料形态与表面状态待现场确认",
        ManufacturingFamily.INJECTION_MOLDING: "；粒料牌号、干燥与回料规则待现场确认",
        ManufacturingFamily.ASSEMBLY: "；具体 BOM、替代件与齐套状态待确认",
    }
    return f"{material}{suffixes[family]}"[:240]


def build_classroom_reference_profile(facts: DrawingFactsV1) -> ClassroomReferenceProfile:
    family, confidence, match_reasons = _infer_family(facts)
    defaults = _DEFAULTS[family]
    sources = _source_catalog(family)
    source_ids = {item.id for item in sources}
    material_sources = ["nbs-ppi-2026-06"]
    if "shfe-daily-market" in source_ids:
        material_sources.append("shfe-daily-market")

    return ClassroomReferenceProfile(
        analysis_id=facts.analysis_id,
        manufacturing_family=family,
        match_confidence=confidence,
        match_reasons=match_reasons,
        quantity=defaults.quantity,
        material_form=_material_form(facts, family, defaults.material_form),
        equipment_capability=defaults.equipment_capability,
        inspection_capability=defaults.inspection_capability,
        special_requirements=defaults.special_requirements,
        quote_inputs=PreQuoteRequest.model_validate(defaults.quote_inputs),
        parameter_basis=[
            ReferenceParameterBasis(
                fields=["net_weight_kg", "material_unit_price", "material_loss_rate_pct"],
                basis="净重、材料单价和损耗是可编辑课堂假设；公开统计/市场数据只用于趋势校准，未冒充供应商采购价。",
                source_ids=material_sources,
            ),
            ReferenceParameterBasis(
                fields=["setup_hours", "processing_minutes_per_part", "machine_hourly_rate", "tooling_cost", "outsourcing_cost"],
                basis="按制造成本分解方法设置课堂工时、设备综合费率与批次成本；不是企业实际工时定额。",
                source_ids=["nist-cost-guide"],
            ),
            ReferenceParameterBasis(
                fields=["inspection_packaging_per_part", "logistics_cost", "overhead_rate_pct", "risk_rate_pct", "target_margin_pct"],
                basis="为让学员理解完整公式而设定的可编辑课堂假设；无外部数据能代替本企业费率与目标毛利。",
                source_ids=[],
            ),
        ],
        sources=sources,
        assumptions=[
            (
                "AI 使用本次图纸提取事实匹配制造类型；参考数据来自带来源、可降级的版本化课堂资料包。"
                if facts.source_status == "ai_extracted"
                else "AI 使用人工定稿后的图纸事实匹配制造类型；参考数据来自版本化课堂资料包。"
            ),
            "数量、设备、工时、费率和成本均为可编辑课堂假设；首次运行无需手工填写。",
            "公开来源用于成本结构、价格趋势与标准索引，不包含企业库存、工时定额、设备折旧或供应商询价。",
        ],
        boundary=(
            "本参考包仅用于课堂演示和方法验证；不是供应商询价、企业费率、投产参数或正式报价。"
            "工艺路线和报价可以独立生成，但正式业务仍必须替换为企业现场能力、工时、费率和询价。"
        ),
    )
