"""Independent process-route and deterministic quote generators."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from .models import (
    BusinessArtifactsV1,
    CostItem,
    DrawingFactsV1,
    ManufacturingFamily,
    PreQuoteRequest,
    PreQuoteV1,
    ProcessParameterRequirement,
    ProcessPlanDraft,
    ProcessPlanDraftV2,
    ProcessRisk,
    ProcessPlanRequest,
    ProcessSourceFactSnapshot,
    ProcessStepV2,
    ReviewDraftV2,
    RuleReport,
    SourceFact,
)


FAMILY_LABELS = {
    ManufacturingFamily.CNC_MACHINING: "CNC 机加工",
    ManufacturingFamily.SHEET_METAL: "钣金加工",
    ManufacturingFamily.INJECTION_MOLDING: "注塑成型",
    ManufacturingFamily.ASSEMBLY: "装配",
}

FIELD_CORRECTION_PREFIX = "field:"


def human_field_corrections(
    *,
    rules: RuleReport,
    decisions: list[dict],
) -> dict[str, str]:
    """Return accepted correction values without mutating the AI draft.

    Corrections attached to rule findings remain supported. An explicit
    ``field:<name>`` correction is applied last so the engineer's dedicated
    field edit has deterministic precedence.
    """

    issues_by_id = {item.id: item for item in rules.issues}
    corrections: dict[str, str] = {}
    for decision in decisions:
        corrected = (decision.get("corrected_value") or "").strip()
        issue = issues_by_id.get(decision.get("finding_id"))
        if decision.get("decision") == "corrected" and corrected and issue and issue.field:
            corrections[issue.field] = corrected
    for decision in decisions:
        corrected = (decision.get("corrected_value") or "").strip()
        finding_id = str(decision.get("finding_id") or "")
        if (
            decision.get("decision") == "corrected"
            and corrected
            and finding_id.startswith(FIELD_CORRECTION_PREFIX)
        ):
            corrections[finding_id.removeprefix(FIELD_CORRECTION_PREFIX)] = corrected
    return corrections


def build_effective_review_draft(
    *,
    draft: ReviewDraftV2,
    rules: RuleReport,
    decisions: list[dict],
) -> ReviewDraftV2:
    """Build a derived review view while preserving the stored AI draft."""

    effective = draft.model_copy(deep=True)
    corrections = human_field_corrections(rules=rules, decisions=decisions)
    for field in effective.fields:
        if field.name in corrections:
            field.value = corrections[field.name]
            field.confidence = 1.0
    return effective


PROCESS_TEMPLATES: dict[ManufacturingFamily, list[tuple[str, str, str, list[str]]]] = {
    ManufacturingFamily.CNC_MACHINING: [
        ("来料与图纸确认", "确认材料、毛坯、版本和加工基准。", "工艺工程师 / 来料检验", ["材料牌号", "图纸版本", "基准与毛坯余量"]),
        ("下料与毛坯准备", "按成品尺寸和余量准备毛坯。", "锯床或下料设备", ["毛坯尺寸", "批次标识", "余量"]),
        ("粗加工", "去除主要余量并建立稳定基准。", "CNC 车床或加工中心", ["装夹方案", "粗加工余量", "变形风险"]),
        ("半精与精加工", "完成关键尺寸、孔系和配合表面。", "CNC 设备 / 适配刀具", ["关键尺寸", "位置精度", "表面粗糙度"]),
        ("去毛刺与清洁", "消除锐边、毛刺和加工残留。", "手工工位 / 清洗设备", ["锐边", "孔口", "清洁度"]),
        ("终检与包装", "按图纸和确认后的检验方案放行。", "量检具 / 检验工位", ["尺寸与公差", "外观", "追溯标识"]),
    ],
    ManufacturingFamily.SHEET_METAL: [
        ("来料与展开确认", "确认板材、厚度、展开方式和基准。", "工艺工程师 / 来料检验", ["材料与厚度", "折弯扣除", "纹理方向"]),
        ("激光切割或冲裁", "获得展开轮廓、孔位和工艺定位。", "激光切割机或数冲", ["轮廓尺寸", "孔距", "热影响与毛刺"]),
        ("去毛刺与整平", "处理切边并保证后续折弯稳定。", "去毛刺 / 整平设备", ["边缘质量", "平面度", "表面保护"]),
        ("折弯成形", "按顺序完成角度和空间尺寸。", "折弯机 / 专用模具", ["折弯顺序", "角度", "回弹补偿"]),
        ("连接与表面处理", "按要求完成焊接、压铆或表面处理。", "焊接 / 压铆 / 外协", ["焊点与变形", "连接强度", "膜层或颜色"]),
        ("终检与包装", "核对成形尺寸、外观和标识。", "检验工位", ["关键尺寸", "外观", "包装防护"]),
    ],
    ManufacturingFamily.INJECTION_MOLDING: [
        ("产品与模具条件确认", "确认材料、收缩率、外观面和模具状态。", "工艺工程师 / 模具工程师", ["材料牌号", "外观面", "收缩率与脱模"]),
        ("原料准备", "完成干燥、配色和批次标识。", "干燥机 / 混料设备", ["干燥条件", "配色比例", "原料批次"]),
        ("试模与参数窗口", "建立温度、压力、速度和保压窗口。", "注塑机 / 模具", ["短射与飞边", "缩痕", "翘曲与熔接线"]),
        ("批量成型", "按获批参数窗口稳定生产。", "注塑机 / 自动取件", ["周期", "关键参数", "首末件"]),
        ("修边与二次工序", "完成去披锋、喷涂、印刷或装配。", "修边工位 / 外协 / 装配工位", ["外观", "二次加工定位", "颜色与附着力"]),
        ("终检与包装", "按限度样件和图纸要求检验。", "检验工位", ["尺寸", "外观", "功能与包装"]),
    ],
    ManufacturingFamily.ASSEMBLY: [
        ("齐套与版本确认", "核对零部件、BOM、图纸版本和替代关系。", "物料员 / 工艺工程师", ["齐套率", "版本", "关键件追溯"]),
        ("零部件预处理", "完成清洁、定位、预装和防错准备。", "预处理工位", ["方向防错", "清洁度", "预装状态"]),
        ("主体装配", "按受控顺序完成连接和定位。", "装配工位 / 工装", ["装配顺序", "扭矩或压力", "间隙与面差"]),
        ("过程检验", "在不可逆或被遮蔽前确认关键特性。", "检验工位", ["关键连接", "防错结果", "过程记录"]),
        ("功能与外观检验", "验证总成功能、外观和接口。", "测试台 / 检验工位", ["功能", "外观", "接口与标识"]),
        ("包装与入库", "按防护、数量和追溯要求包装。", "包装工位", ["包装防护", "数量", "批次追溯"]),
    ],
}


# Each tuple follows: input state, output state, setup/datum, tooling category,
# quality checks, parameters that the responsible engineer must confirm, source facts.
PROCESS_STEP_DETAILS: dict[ManufacturingFamily, list[tuple[str, str, str, str, list[str], list[str], list[str]]]] = {
    ManufacturingFamily.CNC_MACHINING: [
        ("本次图纸提取事实、版本和参考生产需求", "可用于工艺讨论的参考输入清单", "核对设计基准、加工基准和检验基准是否能互相转换", "图纸、3D 模型、材料证明和工艺评审清单", ["核对图号/名称/版本", "核对材料证明与图纸要求", "标出关键尺寸、公差和表面要求"], ["毛坯余量", "基准转换方案"], ["part_name", "revision", "material", "dimensions", "tolerances"]),
        ("已确认材料和毛坯方案", "带批次标识、尺寸和余量记录的毛坯", "下料基准应保留后续首道装夹所需定位面", "锯切/下料工具、标识和来料量具", ["毛坯尺寸及余量", "材料批次和方向", "表面缺陷与变形"], ["下料长度", "单边余量", "锯切损耗"], ["material", "dimensions"]),
        ("合格毛坯", "建立稳定基准并保留精加工余量的半成品", "优先建立图纸主要基准；薄壁或细长结构需专门防变形装夹", "加工中心/车床、通用或专用夹具、粗加工刀具", ["基准完整性", "余量均匀性", "夹紧变形和刀具可达性"], ["装夹力", "切削用量", "粗加工余量"], ["dimensions", "tolerances"]),
        ("已建立基准的半成品", "关键尺寸、孔系和配合面达到待检状态的零件", "按关键尺寸链安排装夹；尽量同一装夹完成关联特性", "适配精加工刀具、镗/铰/磨削能力和在线测量", ["关键尺寸与形位公差", "孔系位置和配合", "表面粗糙度与热变形"], ["精加工余量", "刀具补偿", "切削用量", "过程测量频次"], ["dimensions", "tolerances"]),
        ("加工完成、尚未清理的零件", "无伤人锐边、无残屑且可检验的零件", "不得破坏基准、密封面、锐边要求或已达成的关键尺寸", "去毛刺工具、清洗和防锈设施", ["交叉孔和内腔残屑", "棱边/倒角一致性", "清洁与防锈状态"], ["倒角/钝化范围", "清洗介质", "防锈有效期"], ["dimensions"]),
        ("已清洁的待检零件", "带检验记录和追溯标识的待放行产品", "检验装夹和测量基准须与图纸基准建立清晰对应", "通用量具、专用检具、三坐标或表面测量能力", ["首件与巡检记录", "关键尺寸全检/抽检规则", "外观、标识和包装防护"], ["抽样方案", "量具分辨率", "包装防护等级"], ["revision", "dimensions", "tolerances"]),
    ],
    ManufacturingFamily.SHEET_METAL: [
        ("本次图纸提取事实、板材和参考成形需求", "展开与工艺参考输入清单", "确认成品基准、展开基准、纹理方向和外观面", "展开软件、材料证明和工艺评审清单", ["材料/板厚/版本", "折弯内半径与方向", "外观面和保护要求"], ["折弯系数", "展开补偿", "纹理方向"], ["part_name", "revision", "material", "dimensions", "tolerances"]),
        ("已确认展开数据和板材", "轮廓、孔位和工艺定位完成的平板件", "以展开基准定位，保留折弯和后续连接所需工艺边", "激光切割机/数冲、板材支撑和防划伤设施", ["轮廓与孔距", "切口毛刺和热影响", "板面划伤与混料"], ["切割补偿", "最小孔径", "工艺桥/微连接"], ["material", "dimensions", "tolerances"]),
        ("切割后的平板件", "边缘安全、平面状态稳定的待折弯件", "整平不得破坏定位孔、外观面和后续折弯线", "去毛刺机、整平机和表面保护工位", ["边缘质量", "平面度", "保护膜完整性"], ["去毛刺等级", "整平间隙"], ["dimensions"]),
        ("合格待折弯件", "角度、方向和空间尺寸达到待检状态的成形件", "按干涉、累积误差和可测性确定折弯顺序与定位边", "折弯机、匹配模具、角度测量或在线补偿", ["角度和空间尺寸", "回弹与裂纹", "孔到折弯边距离和外观面"], ["模具开口", "折弯顺序", "回弹补偿", "压力/挠度补偿"], ["dimensions", "tolerances"]),
        ("成形合格的零件或子件", "连接和表面处理完成的待终检件", "连接工装应控制定位、热变形和面差；外协前冻结遮蔽与挂点", "焊接/压铆工装及受控表面处理供应能力", ["连接位置与强度", "焊接变形/压痕", "膜厚、颜色和外观"], ["焊接/压铆参数", "表面处理规范", "外协检验项目"], ["material", "dimensions", "tolerances"]),
        ("连接及表面处理完成件", "带尺寸、外观和追溯记录的待放行产品", "成形尺寸需在无外力状态测量，并定义支撑方式", "高度尺、角度尺、检具、三坐标和外观限度样件", ["关键空间尺寸", "外观与膜层", "包装防压伤/防摩擦"], ["抽样方案", "检具重复性", "包装隔离方式"], ["revision", "dimensions", "tolerances"]),
    ],
    ManufacturingFamily.INJECTION_MOLDING: [
        ("本次图纸提取的产品、材料和外观事实", "产品-材料-模具条件参考清单", "确认脱模方向、外观面、基准、分型线和测量状态", "3D 模型、模流/结构评审、材料数据和限度样件", ["材料牌号/颜色/版本", "壁厚、圆角、拔模与倒扣", "关键尺寸和外观分区"], ["材料收缩率", "目标模穴数", "脱模斜度"], ["part_name", "revision", "material", "dimensions", "tolerances"]),
        ("已确认的树脂、色母和回料规则", "状态可追溯、满足成型要求的原料", "按材料批次隔离并避免吸湿、污染和混料", "干燥、混料、称量和批次追溯设施", ["原料批次", "干燥状态", "配色和回料比例"], ["干燥温度/时间", "配色比例", "回料比例"], ["material"]),
        ("合格原料、模具和注塑机", "经工程评审的成型窗口与首件样件", "确认模具定位、顶出、冷却和产品取向；关键尺寸按稳定周期取样", "匹配锁模力/射胶能力的注塑机、模具和过程监控", ["短射/飞边/缩痕", "翘曲/熔接线/烧焦", "关键尺寸与外观"], ["料筒/模具温度", "注射速度/压力", "保压时间/压力", "冷却时间"], ["material", "dimensions", "tolerances"]),
        ("已批准的成型窗口", "稳定批次的成型件", "保持同一模具状态、工艺窗口和取件方式，异常后重新首件确认", "注塑机、模具、机械手和过程参数监控", ["参数趋势与报警", "周期和穴位差异", "首件/巡检/末件"], ["过程窗口上下限", "巡检频次", "停机重启规则"], ["dimensions", "tolerances"]),
        ("合格成型件", "修边、装饰或子装配完成的待终检件", "二次定位不得压伤外观面或引入新的尺寸变形", "修边工装、喷涂/印刷/焊接或装配设施", ["披锋和浇口残留", "颜色/附着力/位置", "二次加工变形"], ["修边限度", "二次工艺窗口", "固化/静置条件"], ["dimensions"]),
        ("完成全部二次工序的产品", "带尺寸、外观、功能和批次记录的待放行产品", "按规定调湿/静置状态和测量基准检验", "量检具、外观光源、功能测试和包装设施", ["关键尺寸与调湿状态", "外观限度样件", "功能、标识和包装"], ["静置时间", "抽样方案", "包装防护等级"], ["revision", "dimensions", "tolerances"]),
    ],
    ManufacturingFamily.ASSEMBLY: [
        ("本次图纸提取的总成、BOM 和零件事实", "齐套、版本和替代关系参考清单", "确认总成基准、关键接口和零件方向", "BOM/图纸/变更记录和齐套检查工具", ["BOM 与图纸版本", "关键件批次", "替代件授权和缺件状态"], ["齐套规则", "关键件追溯范围"], ["part_name", "revision", "dimensions", "tolerances"]),
        ("齐套且状态合格的零部件", "可防错定位、清洁并完成预装的零件", "利用形状、颜色、条码或工装避免方向和型号混装", "清洁、预装、防错和物料标识工位", ["方向/型号防错", "清洁度和表面状态", "预装到位"], ["清洁标准", "防错验证频次"], ["material", "dimensions"]),
        ("完成预处理的零件", "连接、定位和接口满足待检要求的总成", "按基准链和不可逆工序安排装配顺序，先确认后遮蔽", "装配工位、定位工装、扭矩/压力工具", ["装配顺序", "连接参数与防松", "间隙、面差和接口"], ["扭矩/压力", "装配顺序", "工装定位精度"], ["dimensions", "tolerances"]),
        ("关键连接尚可见的在制总成", "关键过程特性已记录的总成", "在遮蔽、粘接固化或不可逆操作前设置停检点", "过程检验工位、量检具和数据采集", ["关键连接和防错结果", "间隙/面差/位置", "过程记录完整性"], ["停检点", "抽样/全检规则", "返工边界"], ["dimensions", "tolerances"]),
        ("过程检验合格总成", "功能、外观和接口已验证的成品", "测试状态、安装姿态和接口条件必须可重复", "功能测试台、检具、外观光源和限度样件", ["功能输出", "接口匹配", "外观、异响和标识"], ["测试条件", "合格判据", "重复测试规则"], ["dimensions", "tolerances"]),
        ("检验合格成品", "带数量、包装和追溯记录的待入库产品", "包装定位不得对关键接口和外观面施加载荷", "包装工位、计数/扫码和防护材料", ["数量和附件", "批次追溯", "防错装、防磕碰和储运要求"], ["包装数量", "堆码限制", "储运条件"], ["revision"]),
    ],
}


def build_drawing_facts(
    *,
    analysis_id: str,
    business_status: str,
    draft: ReviewDraftV2,
    rules: RuleReport,
    decisions: list[dict],
    source_status: str = "human_finalized",
) -> DrawingFactsV1:
    facts_by_name = {
        item.name: SourceFact(
            name=item.name,
            value=item.value,
            confidence=item.confidence,
            evidence_ids=list(item.evidence_ids),
        )
        for item in draft.fields
    }
    corrections = human_field_corrections(rules=rules, decisions=decisions)
    for field_name, corrected in corrections.items():
        if field_name not in facts_by_name:
            continue
        existing = facts_by_name[field_name]
        facts_by_name[field_name] = SourceFact(
            name=existing.name,
            value=corrected,
            confidence=1.0,
            evidence_ids=existing.evidence_ids,
            source="human_correction",
        )

    missing = [name for name, item in facts_by_name.items() if not item.value.strip()]
    missing.extend(["manufacturing_family", "quantity", "machine_and_tooling_capability", "validated_geometry"])
    return DrawingFactsV1(
        analysis_id=analysis_id,
        document_type=draft.document_type,
        review_business_status=business_status,
        source_status=source_status,
        facts=list(facts_by_name.values()),
        missing_for_process=list(dict.fromkeys(missing)),
        boundary=(
            "These facts come from a human-finalized review record. "
            if source_status == "human_finalized"
            else "These facts were extracted by AI for this independent feature run. "
        ) + (
            "They remain source-grounded reference inputs, not a released process plan, "
            "NC program, or binding quotation."
        ),
    )


def build_process_plan(
    *,
    analysis_id: str,
    facts: DrawingFactsV1,
    request: ProcessPlanRequest,
) -> ProcessPlanDraftV2:
    source_facts = [
        ProcessSourceFactSnapshot(
            name=item.name,
            value=item.value.strip(),
            source=item.source,
            evidence_ids=list(item.evidence_ids),
        )
        for item in facts.facts
    ]
    values = {item.name: item.value.strip() for item in facts.facts}
    digest_source = json.dumps(
        facts.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    source_fact_digest = hashlib.sha256(digest_source).hexdigest()

    equipment_note = request.equipment_capability.strip()
    inspection_note = request.inspection_capability.strip()
    request_source = "reference_profile" if facts.source_status == "ai_extracted" else "human_input"
    steps: list[ProcessStepV2] = []
    for index, (base, detail) in enumerate(
        zip(
            PROCESS_TEMPLATES[request.manufacturing_family],
            PROCESS_STEP_DETAILS[request.manufacturing_family],
            strict=True,
        ),
        start=1,
    ):
        operation, purpose, resource, control_points = base
        input_state, output_state, setup, tooling, quality_checks, pending_parameters, source_fields = detail
        parameters = [
            ProcessParameterRequirement(
                name=name,
                target="待工艺负责人结合设备、工装、材料批次和试制结果确认",
                source="pending",
                status="needs_confirmation",
            )
            for name in pending_parameters
        ]
        if index == 1:
            parameters.insert(
                0,
                ProcessParameterRequirement(
                    name="生产数量",
                    target=str(request.quantity),
                    unit="件",
                    source=request_source,
                    status="known",
                ),
            )
            parameters.insert(
                1,
                ProcessParameterRequirement(
                    name="材料/毛坯形态",
                    target=request.material_form or "待工艺工程师确认",
                    source=request_source,
                    status="known" if request.material_form.strip() else "needs_confirmation",
                ),
            )
        steps.append(
            ProcessStepV2(
                sequence=index,
                operation=operation,
                purpose=purpose,
                input_state=input_state,
                output_state=output_state,
                equipment_capability=(
                    f"{resource}；现场已知能力：{equipment_note}"
                    if equipment_note
                    else f"{resource}；具体型号、行程、精度和产能待现场确认"
                ),
                setup_and_datum=setup,
                tooling_category=tooling,
                key_characteristics=list(control_points),
                quality_checks=(
                    [*quality_checks, f"现场检验条件：{inspection_note}"]
                    if inspection_note
                    else [*quality_checks, "量具/检具能力、测量方法和环境条件待确认"]
                ),
                parameters=parameters,
                source_fact_fields=[name for name in source_fields if name in values],
            )
        )

    missing_inputs = [
        item
        for item in facts.missing_for_process
        if item not in {"manufacturing_family", "quantity"}
    ]
    if equipment_note:
        missing_inputs = [item for item in missing_inputs if item != "machine_and_tooling_capability"]
    if not inspection_note:
        missing_inputs.append("inspection_capability")
    missing_inputs = list(dict.fromkeys(missing_inputs))

    open_questions = [
        "是否已有与本零件匹配的 3D 模型、工装基准和可达性/干涉验证？",
        "关键特性采用全检、首件加巡检还是统计抽样，判定依据是什么？",
        "哪些工序需要外协，供应商能力、交付状态和复验项目是什么？",
    ]
    if not equipment_note:
        open_questions.append("现场可用设备、行程/吨位/锁模力、精度、刀具或模具能力是什么？")
    if not inspection_note:
        open_questions.append("现场可用量具、检具、三坐标或功能测试能力是什么？")
    if not values.get("material"):
        open_questions.append("材料牌号、状态、替代料和材料证明要求是什么？")

    risks = [
        ProcessRisk(
            code="PR-01",
            level="high",
            concern="二维图纸不能证明全部几何、干涉和刀具/工装可达性。",
            impact="直接投产可能出现漏加工、碰撞、装夹失败或尺寸链不成立。",
            verification_action="补充 3D 模型，完成基准、可达性、干涉和必要仿真/试制评审。",
            owner_role="工艺负责人",
        ),
        ProcessRisk(
            code="PR-02",
            level="high" if not equipment_note else "medium",
            concern="工艺路线尚未与现场设备、工装、刀具/模具和产能逐项匹配。",
            impact="计划中的工序可能无法稳定加工，或成本与节拍偏离实际。",
            verification_action="按每道工序核对设备能力并记录可用资源、替代方案和瓶颈。",
            owner_role="制造/工艺负责人",
        ),
        ProcessRisk(
            code="PR-03",
            level="high" if not inspection_note else "medium",
            concern="关键特性的测量方法、量具能力和抽样规则尚未冻结。",
            impact="生产完成后可能无法用一致方法证明产品满足图纸要求。",
            verification_action="形成检验特性清单，完成量具适用性和必要的测量系统评估。",
            owner_role="质量负责人",
        ),
    ]
    if facts.review_business_status != "pass":
        upstream_status = {
            "needs_review": "仍有事项需要工程师确认",
            "blocked": "存在必须先处理的问题",
        }.get(facts.review_business_status, facts.review_business_status)
        risks.insert(
            0,
            ProcessRisk(
                code="PR-00",
                level="high",
                concern=f"上游图纸审核结论为“{upstream_status}”，仍有问题需要处理。",
                impact="未关闭的问题可能使工艺输入发生变化或导致错误制造。",
                verification_action="关闭上游问题并确认图纸/事实版本，再复核或重生成本工艺草案。",
                owner_role="设计与工艺负责人",
            ),
        )

    assumptions = [
        f"制造类型按图纸事实与公开参考资料匹配为：{FAMILY_LABELS[request.manufacturing_family]}。",
        f"本批数量按 {request.quantity} 件规划。",
        f"材料/毛坯形态暂按“{request.material_form or '待确认'}”。",
        (
            "路线由程序模板结合 AI 提取事实和公开参考资料生成；未自动生成机床程序、刀路或投产参数。"
            if facts.source_status == "ai_extracted"
            else "路线由程序模板结合已确认的图纸事实生成；未自动生成机床程序、刀路或投产参数。"
        ),
    ]
    if request.special_requirements.strip():
        assumptions.append(f"人工补充要求：{request.special_requirements.strip()}")
    warnings = [
        "必须由工艺工程师结合设备、刀具、工装和现场能力逐项确认。",
        "二维图像不能替代三维几何、干涉、可达性、仿真和试制验证。",
    ]
    if facts.review_business_status != "pass":
        readable_status = {
            "needs_review": "仍有事项需要工程师确认",
            "blocked": "存在必须先处理的问题",
        }.get(facts.review_business_status, facts.review_business_status)
        warnings.append(f"上游图纸审核为“{readable_status}”；不得直接投产。")
    if not values.get("material"):
        warnings.append("材料未形成可靠事实，工艺与报价必须暂缓。")
    if not values.get("dimensions") or not values.get("tolerances"):
        warnings.append("尺寸或公差信息不完整，需要补齐受控依据。")
    family_label = FAMILY_LABELS[request.manufacturing_family]
    part_name = values.get("part_name") or "当前零件/总成"
    material = values.get("material") or request.material_form or "待确认材料"
    inspection_strategy = [
        "首件：按已确认的关键特性清单逐项记录，首件通过后才能进入后续批量。",
        "过程：在基准建立、不可逆/遮蔽工序和关键尺寸完成后设置检查点。",
        "终检：按图纸版本、测量基准、环境和抽样规则形成可追溯记录。",
    ]
    if inspection_note:
        inspection_strategy.append(f"现场已知检验能力：{inspection_note}")
    external_processes = {
        ManufacturingFamily.CNC_MACHINING: ["热处理、表面处理或特殊检测仅在图纸明确要求并完成外协评审后纳入。"],
        ManufacturingFamily.SHEET_METAL: ["焊接、喷涂、电镀等外协需冻结遮蔽、挂点、膜层和复验要求。"],
        ManufacturingFamily.INJECTION_MOLDING: ["模具制造/修模、喷涂、印刷、焊接等二次工序需单独确认能力和验收标准。"],
        ManufacturingFamily.ASSEMBLY: ["特殊连接、校准或功能测试外协需定义接口状态、记录和返工边界。"],
    }[request.manufacturing_family]
    return ProcessPlanDraftV2(
        analysis_id=analysis_id,
        manufacturing_family=request.manufacturing_family,
        quantity=request.quantity,
        material_form=request.material_form or "待工艺工程师确认",
        equipment_capability=equipment_note,
        inspection_capability=inspection_note,
        special_requirements=request.special_requirements,
        route_summary=(
            f"面向 {part_name} 的 {family_label} 路线草案，按 {request.quantity} 件、"
            f"{material} 的当前受控输入规划。路线覆盖输入确认、主要制造、过程检验、"
            "终检与包装；所有设备匹配、工装基准和投产参数仍须由现场负责人确认。"
        ),
        source_fact_digest=source_fact_digest,
        source_facts=source_facts,
        missing_inputs=missing_inputs,
        open_questions=open_questions,
        steps=steps,
        risks=risks,
        inspection_strategy=inspection_strategy,
        external_processes=external_processes,
        assumptions=assumptions,
        warnings=warnings,
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(value))


def build_prequote(
    *,
    analysis_id: str,
    process_plan: ProcessPlanDraft,
    request: PreQuoteRequest,
    input_source: str = "operator",
) -> PreQuoteV1:
    quantity = Decimal(process_plan.quantity)
    loss_rate = _decimal(request.material_loss_rate_pct) / Decimal(100)
    material_cost = _decimal(request.net_weight_kg) * (Decimal(1) + loss_rate) * _decimal(request.material_unit_price) * quantity
    setup_cost = _decimal(request.setup_hours) * _decimal(request.machine_hourly_rate)
    processing_cost = (
        _decimal(request.processing_minutes_per_part)
        / Decimal(60)
        * _decimal(request.machine_hourly_rate)
        * quantity
    )
    tooling_cost = _decimal(request.tooling_cost)
    outsourcing_cost = _decimal(request.outsourcing_cost)
    inspection_cost = _decimal(request.inspection_packaging_per_part) * quantity
    logistics_cost = _decimal(request.logistics_cost)
    cost_values = [
        ("material", "材料与损耗", material_cost, f"{request.net_weight_kg} kg/件 × {1 + request.material_loss_rate_pct / 100:.3f} × ¥{request.material_unit_price}/kg × {process_plan.quantity} 件"),
        ("setup", "调机准备", setup_cost, f"{request.setup_hours} 小时 × ¥{request.machine_hourly_rate}/小时"),
        ("processing", "加工工时", processing_cost, f"{request.processing_minutes_per_part} 分钟/件 × ¥{request.machine_hourly_rate}/小时 × {process_plan.quantity} 件"),
        ("tooling", "工装刀具", tooling_cost, "本批一次性输入"),
        ("outsourcing", "外协费用", outsourcing_cost, "本批一次性输入"),
        ("inspection_packaging", "检验与包装", inspection_cost, f"¥{request.inspection_packaging_per_part}/件 × {process_plan.quantity} 件"),
        ("logistics", "物流费用", logistics_cost, "本批一次性输入"),
    ]
    cost_items = [
        CostItem(code=code, label=label, amount=float(_money(value)), basis=basis)
        for code, label, value, basis in cost_values
    ]
    direct_cost = sum((value for _, _, value, _ in cost_values), Decimal(0))
    overhead_cost = direct_cost * _decimal(request.overhead_rate_pct) / Decimal(100)
    risk_cost = (direct_cost + overhead_cost) * _decimal(request.risk_rate_pct) / Decimal(100)
    total_cost = direct_cost + overhead_cost + risk_cost
    margin_rate = _decimal(request.target_margin_pct) / Decimal(100)
    target_revenue = total_cost / (Decimal(1) - margin_rate)
    unit_prequote = target_revenue / quantity
    return PreQuoteV1(
        analysis_id=analysis_id,
        process_plan_version=process_plan.schema_version,
        quantity=process_plan.quantity,
        inputs=request,
        cost_items=cost_items,
        direct_cost=float(_money(direct_cost)),
        overhead_cost=float(_money(overhead_cost)),
        risk_cost=float(_money(risk_cost)),
        total_cost=float(_money(total_cost)),
        target_revenue=float(_money(target_revenue)),
        unit_prequote=float(_money(unit_prequote)),
        assumptions=[
            f"数量按当前估算场景：{process_plan.quantity} 件。",
            (
                "材料、工时、费率和附加费用由 AI 结合带来源的课堂参考包自动补齐，可在结果页查看和修改。"
                if input_source == "ai_public_reference"
                else "材料、工时、费率和附加费用均是本次由操作者确认的输入；可来自版本化课堂参考包。"
            ),
            "目标收入按总成本 ÷ (1 - 目标毛利率) 计算。",
        ],
        warnings=[
            "这是未含税的内部预报价，不是对客户生效的正式报价。",
            "未自动计入汇率波动、账期、模具摊销、加急、售后和合同风险。",
            "正式报价前必须由业务、工艺、采购和财务共同复核。",
        ],
    )


def build_artifacts(
    *,
    facts: DrawingFactsV1,
    process_plan: ProcessPlanDraft | None = None,
    prequote: PreQuoteV1 | None = None,
) -> BusinessArtifactsV1:
    return BusinessArtifactsV1(
        drawing_facts=facts,
        process_plan=process_plan,
        prequote=prequote,
    )
