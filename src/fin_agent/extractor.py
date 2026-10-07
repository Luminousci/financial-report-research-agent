from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .io_utils import normalize_text, parse_decimal
from .models import FinancialFact, NonRecurringItem, PageRecord, ParsedDocument, SourceRef, TableRecord


@dataclass(slots=True)
class MetricDefinition:
    code: str
    name: str
    aliases: list[str]
    kind: str


class FinancialTableExtractor:
    def __init__(self, dictionary_path: Path):
        raw = json.loads(dictionary_path.read_text(encoding="utf-8"))
        self.metrics = [
            MetricDefinition(code=code, name=item["name"], aliases=item["aliases"], kind=item["kind"])
            for code, item in raw.items()
        ]
        self.metrics.sort(
            key=lambda metric: max(len(normalize_text(alias)) for alias in metric.aliases),
            reverse=True,
        )

    def _match(self, label: str | None) -> MetricDefinition | None:
        compact = normalize_text(label)
        if not compact:
            return None
        compact = re.sub(r"^(?:[一二三四五六七八九十]+[、.]|\(?[0-9]+\)?[、.])", "", compact)
        compact = re.sub(r"^(?:其中[:：]?|加[:：]?|减[:：]?)", "", compact)
        canonical = re.sub(r"(?<=的)(?:本年|当期|本期|年度)(?=净利润)", "", compact)
        for metric in self.metrics:
            for alias in metric.aliases:
                alias_compact = normalize_text(alias)
                if (
                    compact.lower() == alias_compact.lower()
                    or canonical.lower() == alias_compact.lower()
                    or compact.lower().startswith((alias_compact + "(").lower())
                    or compact.lower().startswith((alias_compact + "（").lower())
                    or (
                        len(alias_compact) >= 12
                        and compact.lower().startswith(alias_compact.lower())
                    )
                    or (
                    metric.code == "operating_cash_flow" and compact.startswith("经营活动产生的现金流")
                    )
                ):
                    return metric
        return None

    @staticmethod
    def _unit_from_text(text: str, default_scale: Decimal) -> tuple[str, Decimal]:
        compact = normalize_text(text)
        if "RMB" in text.upper() and "MILLION" in text.upper():
            return "百万元", Decimal("1000000")
        if "百万元" in compact or "RMB’000,000" in compact or "RMB'000,000" in compact:
            return "百万元", Decimal("1000000")
        if "千元" in compact or "RMB’000" in compact or "RMB'000" in compact:
            return "千元", Decimal("1000")
        # Only accept an explicit unit declaration. Narrative text frequently
        # contains words such as “亿元”; treating any such occurrence as the
        # table unit would multiply already-denominated yuan values again.
        match = re.search(r"单位[:：]?(?:人民币)?([^币种]{1,12})", compact)
        unit_text = match.group(1) if match else ""
        if "亿元" in unit_text:
            return "亿元", Decimal("100000000")
        if "百万元" in unit_text:
            return "百万元", Decimal("1000000")
        if "万元" in unit_text:
            return "万元", Decimal("10000")
        if "元" in unit_text:
            return "元", Decimal("1")
        if default_scale == Decimal("1000000"):
            return "百万元", default_scale
        if default_scale == Decimal("10000"):
            return "万元", default_scale
        return "元", default_scale

    @staticmethod
    def _annual_column_indices(
        table: TableRecord, report_type: str | None = None
    ) -> tuple[int, int | None, int | None, bool]:
        """Map data-cell indices for annual-report tables.

        The returned indices are relative to ``row[1:]``.  This handles the
        three common layouts used in annual reports: headline summaries,
        asset-composition tables, and statutory statements with an ``附注``
        column.  The final flag marks annual quarterly-breakdown tables, which
        must not be mislabeled as full-year facts.
        """
        if not table.rows:
            return 0, 1, 2, False
        headers = [normalize_text(cell or "") for cell in table.rows[0]]
        header_text = " ".join(headers)
        if "第一季度" in header_text and "第二季度" in header_text:
            return 0, None, None, True
        data_headers = headers[1:]
        if "附注" in data_headers:
            note_index = data_headers.index("附注")
            current_index = note_index + 1
            prior_index = current_index + 1 if len(data_headers) > current_index + 1 else None
            return current_index, prior_index, None, False
        year_end_index = next(
            (index for index, value in enumerate(data_headers) if re.fullmatch(r"20\d{2}年末", value)),
            None,
        )
        year_start_index = next(
            (index for index, value in enumerate(data_headers) if re.fullmatch(r"20\d{2}年初", value)),
            None,
        )
        if year_end_index is not None and year_start_index is not None:
            return year_end_index, year_start_index, None, False
        # Continuation pages of statutory statements often omit the header but
        # preserve the four-column layout: label, note, current, prior.
        statement_layout_rows = 0
        for row in table.rows[:12]:
            if len(row) < 4:
                continue
            note = parse_decimal(row[1])
            current = parse_decimal(row[2])
            prior = parse_decimal(row[3])
            if current is not None and prior is not None and (
                row[1] in (None, "") or (note is not None and abs(note) < 1000)
            ):
                statement_layout_rows += 1
        explicit_period_header = any(
            any(token in value for token in ("年", "本期", "上期", "报告期"))
            for value in data_headers
        )
        if report_type in {"annual", "semiannual"} and statement_layout_rows >= 1 and not explicit_period_header:
            return 1, 2, None, False
        current_index = next(
            (index for index, value in enumerate(data_headers) if value.startswith("本期期末数")),
            0,
        )
        prior_index = next(
            (
                index
                for index, value in enumerate(data_headers)
                if value.startswith(("上期期末数", "上年期末数"))
            ),
            1 if len(data_headers) > 1 else None,
        )
        change_index = next(
            (
                index
                for index, value in enumerate(data_headers)
                if ("增减" in value or "变动比例" in value) and "%" in value
            ),
            None,
        )
        return current_index, prior_index, change_index, False

    @staticmethod
    def _period_label(year: int | None, report_type: str, period_type: str) -> str:
        prefix = f"FY{year}" if year else "FY_UNKNOWN"
        if report_type == "q1":
            return f"{prefix}_Q1"
        if report_type == "q3":
            return f"{prefix}_Q3_single" if period_type == "single_quarter" else f"{prefix}_YTD_Q3"
        if report_type == "semiannual":
            return f"{prefix}_H1"
        if report_type == "annual":
            return prefix
        return f"{prefix}_UNKNOWN"

    def _source(
        self,
        parsed: ParsedDocument,
        table: TableRecord,
        row_index: int,
        label: str,
        raw_value: str,
    ) -> SourceRef:
        return SourceRef(
            document_id=parsed.meta.document_id,
            file_name=parsed.meta.file_name,
            page=table.page,
            table_index=table.table_index,
            row_index=row_index,
            raw_label=label,
            raw_value=raw_value,
            evidence_text=" | ".join(cell or "" for cell in table.rows[row_index]),
            extraction_method=table.extraction_method,
            confidence=0.98,
        )

    def _make_fact(
        self,
        parsed: ParsedDocument,
        table: TableRecord,
        row_index: int,
        metric: MetricDefinition,
        raw_label: str,
        raw_value: str,
        value: Decimal,
        unit: str,
        scale: Decimal,
        period_label: str,
        period_type: str,
        comparison_kind: str,
    ) -> FinancialFact:
        effective_unit = unit
        effective_scale = scale
        if metric.kind != "ratio" and scale > 1 and abs(value) >= Decimal("1000000000"):
            # Some pages contain an unrelated unit declaration while the table
            # itself already prints full yuan amounts.  Avoid multiplying an
            # obviously base-denominated value a second time.
            effective_unit = "元"
            effective_scale = Decimal("1")
        return FinancialFact(
            metric_code=metric.code,
            metric_name=metric.name,
            value=value,
            raw_value=raw_value,
            unit=effective_unit,
            currency=parsed.meta.currency,
            unit_scale=effective_scale,
            period_label=period_label,
            period_type=period_type,
            comparison_kind=comparison_kind,
            source=self._source(parsed, table, row_index, raw_label, raw_value),
        )

    def extract(self, parsed: ParsedDocument) -> list[FinancialFact]:
        if parsed.meta.report_type == "business_update":
            return []
        page_text = {page.page: page.text for page in parsed.pages}
        facts: list[FinancialFact] = []
        year = parsed.meta.report_year

        for table in parsed.tables:
            unit, scale = self._unit_from_text(
                " ".join(
                    [page_text.get(table.page, "")] +
                    [str(cell or "") for row in table.rows[:2] for cell in row]
                ),
                parsed.meta.unit_scale,
            )
            mapped_current_index, mapped_prior_index, mapped_change_index, annual_quarterly_table = (
                self._annual_column_indices(table, parsed.meta.report_type)
            )
            if parsed.meta.report_type == "annual" and annual_quarterly_table:
                continue
            for row_index, row in enumerate(table.rows):
                if not row:
                    continue
                raw_label = row[0] or ""
                metric = self._match(raw_label)
                if metric is None:
                    continue

                cells = ["" if cell is None else str(cell).strip() for cell in row[1:]]
                if not cells:
                    continue

                if metric.kind == "ratio":
                    metric_unit = "元/股" if metric.code in {"basic_eps", "diluted_eps"} else "%"
                    metric_scale = Decimal("1")
                else:
                    metric_unit = unit
                    metric_scale = scale

                if metric.kind == "stock":
                    current_index = mapped_current_index
                    prior_index = mapped_prior_index
                    change_index = mapped_change_index
                    current_cell = cells[current_index] if current_index < len(cells) else ""
                    current = parse_decimal(current_cell)
                    if current is not None:
                        period = self._period_label(year, parsed.meta.report_type, "point_in_time")
                        facts.append(
                            self._make_fact(
                                parsed,
                                table,
                                row_index,
                                metric,
                                raw_label,
                                current_cell,
                                current,
                                metric_unit,
                                metric_scale,
                                period,
                                "point_in_time",
                                "value",
                            )
                        )
                        if prior_index is not None and prior_index < len(cells):
                            prior_cell = cells[prior_index]
                            prior = parse_decimal(prior_cell)
                            if prior is not None:
                                facts.append(
                                    self._make_fact(
                                        parsed,
                                        table,
                                        row_index,
                                        metric,
                                        raw_label,
                                        prior_cell,
                                        prior,
                                        metric_unit,
                                        metric_scale,
                                        period,
                                        "point_in_time",
                                        "prior_value",
                                    )
                                )
                        change_cell = cells[change_index] if change_index is not None and change_index < len(cells) else ""
                        change = parse_decimal(change_cell)
                        if change is not None and change_cell not in {current_cell, prior_cell if prior_index is not None and prior_index < len(cells) else ""}:
                            facts.append(
                                self._make_fact(
                                    parsed,
                                    table,
                                    row_index,
                                    metric,
                                    raw_label,
                                    change_cell,
                                    change,
                                    "%",
                                    Decimal("1"),
                                    period,
                                    "point_in_time",
                                    "reported_yoy_pct",
                                )
                            )
                    continue

                report_type = parsed.meta.report_type
                header_text = normalize_text(" ".join(cell or "" for header_row in table.rows[:2] for cell in header_row))
                main_quarter_table = (
                    ("本报告期" in header_text and "年初至报告期末" in header_text)
                    or (
                        report_type == "q3"
                        and table.page <= 3
                        and len(cells) >= 4
                        and parse_decimal(cells[3]) is not None
                    )
                )
                if report_type == "q3" and main_quarter_table and len(cells) >= 4:
                    quarter_value = parse_decimal(cells[0])
                    quarter_change = parse_decimal(cells[1])
                    ytd_value = parse_decimal(cells[2])
                    ytd_change = parse_decimal(cells[3])
                    if quarter_value is not None:
                        period = self._period_label(year, report_type, "single_quarter")
                        facts.append(self._make_fact(parsed, table, row_index, metric, raw_label, cells[0], quarter_value, metric_unit, metric_scale, period, "single_quarter", "value"))
                        if quarter_change is not None:
                            facts.append(self._make_fact(parsed, table, row_index, metric, raw_label, cells[1], quarter_change, "%", Decimal("1"), period, "single_quarter", "reported_yoy_pct"))
                    if ytd_value is not None:
                        period = self._period_label(year, report_type, "cumulative")
                        facts.append(self._make_fact(parsed, table, row_index, metric, raw_label, cells[2], ytd_value, metric_unit, metric_scale, period, "cumulative", "value"))
                        if ytd_change is not None:
                            facts.append(self._make_fact(parsed, table, row_index, metric, raw_label, cells[3], ytd_change, "%", Decimal("1"), period, "cumulative", "reported_yoy_pct"))
                    continue

                period_type = "annual" if report_type == "annual" else (
                    "single_quarter" if report_type == "q1" else "cumulative"
                )
                period = self._period_label(year, report_type, period_type)
                current_index = mapped_current_index if report_type == "annual" else 0
                prior_index = mapped_prior_index if report_type == "annual" else 1
                change_index = mapped_change_index if report_type == "annual" else 2
                current_cell = cells[current_index] if current_index < len(cells) else ""
                current = parse_decimal(current_cell)
                if current is None:
                    continue
                facts.append(self._make_fact(parsed, table, row_index, metric, raw_label, current_cell, current, metric_unit, metric_scale, period, period_type, "value"))

                second_cell = cells[prior_index] if prior_index is not None and prior_index < len(cells) else ""
                second = parse_decimal(second_cell)
                third_cell = cells[change_index] if change_index is not None and change_index < len(cells) else ""
                third = parse_decimal(third_cell)

                if second is not None:
                    facts.append(self._make_fact(parsed, table, row_index, metric, raw_label, second_cell, second, metric_unit, metric_scale, period, period_type, "prior_value"))
                if third is not None:
                    facts.append(self._make_fact(parsed, table, row_index, metric, raw_label, third_cell, third, "%", Decimal("1"), period, period_type, "reported_yoy_pct"))

        facts.extend(self._extract_text_fallback(parsed, facts))
        # A report can repeat the same metric in the headline summary,
        # consolidated statements and parent-company statements.  Until scope
        # is explicit in every source table, retain the first (normally the
        # headline/consolidated) occurrence as the canonical fact so later
        # parent-only values cannot overwrite calculations.
        deduplicated: list[FinancialFact] = []
        seen: set[tuple[str, str, str]] = set()

        def source_priority(fact: FinancialFact) -> tuple[int, int]:
            page = fact.source.page if fact.source else 10**9
            method = 0 if fact.source and fact.source.extraction_method == "pdfplumber" else 1
            if fact.metric_code in {"total_assets", "total_liabilities", "total_equity"}:
                # Consolidated balance-sheet pages precede parent-company and
                # note tables, so page order is the stronger signal here.
                return page, method
            # For other stock metrics, a structured table outranks an earlier
            # narrative mention that may describe a change rather than a balance.
            return method, page

        ordered_facts = sorted(
            facts,
            key=source_priority,
        )
        for fact in ordered_facts:
            key = (
                fact.metric_code,
                fact.period_label,
                fact.comparison_kind,
            )
            if key not in seen:
                seen.add(key)
                deduplicated.append(fact)
        return deduplicated

    @staticmethod
    def _alias_pattern(alias: str) -> re.Pattern[str]:
        tokens = re.split(r"\s+", alias.strip())
        return re.compile(r"\s*".join(re.escape(token) for token in tokens), re.IGNORECASE)

    def _extract_text_fallback(
        self, parsed: ParsedDocument, existing: list[FinancialFact]
    ) -> list[FinancialFact]:
        if parsed.meta.report_type == "business_update":
            return []
        existing_codes = {fact.metric_code for fact in existing if fact.comparison_kind == "value"}
        fallback: list[FinancialFact] = []
        number_pattern = re.compile(r"\(?[-+]?\d[\d,]*(?:\.\d+)?\)?%?")
        for metric in self.metrics:
            if metric.code in existing_codes and metric.kind != "stock":
                continue
            match_data: tuple[PageRecord, str, str, list[tuple[str, Decimal]]] | None = None
            for page in parsed.pages:
                for alias in metric.aliases:
                    match = None
                    for candidate in self._alias_pattern(alias).finditer(page.text):
                        prefix = re.sub(r"\s+", "", page.text[max(0, candidate.start() - 8):candidate.start()])
                        suffix = re.sub(r"\s+", "", page.text[candidate.end():candidate.end() + 6])
                        if metric.code == "capital_adequacy_ratio" and (
                            prefix.endswith(("核心一级", "一级")) or suffix.startswith("指标")
                        ):
                            continue
                        if metric.code == "total_liabilities" and prefix.endswith(("流动", "非流动")):
                            continue
                        if metric.code == "total_equity" and prefix.endswith(("母公司", "归属于母公司")):
                            continue
                        if (
                            metric.code == "total_liabilities"
                            and normalize_text(alias) == "负债总额"
                            and parsed.meta.industry_profile != "bank"
                        ):
                            continue
                        match = candidate
                        break
                    if match is None:
                        continue
                    evidence = re.sub(r"\s+", " ", page.text[match.start(): match.end() + 220]).strip()
                    tail = page.text[match.end(): match.end() + 220]
                    tokens: list[tuple[str, Decimal]] = []
                    from_to = re.search(
                        r"(?:increased|decreased)\s+from\s+RMB\s*([\d,.]+)\s+million.*?to\s+RMB\s*([\d,.]+)\s+million",
                        tail,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    if from_to:
                        prior_raw, current_raw = from_to.group(1), from_to.group(2)
                        prior_value, current_value = parse_decimal(prior_raw), parse_decimal(current_raw)
                        if prior_value is not None and current_value is not None:
                            tokens = [(current_raw, current_value), (prior_raw, prior_value)]
                    if not tokens:
                        for number_match in number_pattern.finditer(tail):
                            raw = number_match.group(0)
                            plain = raw.strip("()%")
                            if re.fullmatch(r"20\d{2}", plain):
                                continue
                            left = tail[max(0, number_match.start() - 1):number_match.start()]
                            right = tail[number_match.end():number_match.end() + 1]
                            if left in {"(", "（"} and right in {")", "）"}:
                                continue
                            if metric.kind != "ratio" and "," not in raw and len(re.sub(r"\D", "", raw)) < 4:
                                # Statement note numbers and audit-step markers
                                # are not financial amounts.
                                continue
                            value = parse_decimal(raw)
                            if value is not None:
                                tokens.append((raw, value))
                            if len(tokens) >= 2:
                                break
                    if metric.kind == "ratio" and "百分点" in tail and len(tokens) > 1:
                        tokens = tokens[:1]
                    minimum_tokens = 1 if metric.kind == "ratio" else 2
                    if len(tokens) >= minimum_tokens:
                        match_data = (page, alias, evidence, tokens)
                        break
                if match_data:
                    break
            if not match_data:
                continue
            page, alias, evidence, tokens = match_data
            unit, scale = self._unit_from_text(page.text, parsed.meta.unit_scale)
            metric_unit = "元/股" if metric.code in {"basic_eps", "diluted_eps"} else ("%" if metric.kind == "ratio" else unit)
            metric_scale = Decimal("1") if metric.kind == "ratio" else scale
            period_type = "point_in_time" if metric.kind == "stock" else (
                "annual" if parsed.meta.report_type == "annual" else (
                    "single_quarter" if parsed.meta.report_type == "q1" else "cumulative"
                )
            )
            period = self._period_label(parsed.meta.report_year, parsed.meta.report_type, period_type)
            for comparison_kind, (raw, value) in zip(("value", "prior_value"), tokens):
                fact_unit = metric_unit
                fact_scale = metric_scale
                if metric.kind != "ratio":
                    raw_pattern = re.escape(raw)
                    if re.search(raw_pattern + r"\s*(?:人民币)?亿元", evidence):
                        fact_unit, fact_scale = "亿元", Decimal("100000000")
                    elif re.search(raw_pattern + r"\s*(?:人民币)?万元", evidence):
                        fact_unit, fact_scale = "万元", Decimal("10000")
                    elif re.search(raw_pattern + r"\s*(?:人民币)?(?:百万元|million)", evidence, re.IGNORECASE):
                        fact_unit, fact_scale = "百万元", Decimal("1000000")
                    if fact_scale > 1 and abs(value) >= Decimal("1000000000"):
                        fact_unit, fact_scale = "元", Decimal("1")
                source = SourceRef(
                    document_id=parsed.meta.document_id,
                    file_name=parsed.meta.file_name,
                    page=page.page,
                    raw_label=alias,
                    raw_value=raw,
                    evidence_text=evidence,
                    extraction_method="pypdf_text_fallback",
                    confidence=0.86,
                )
                fallback.append(
                    FinancialFact(
                        metric_code=metric.code,
                        metric_name=metric.name,
                        value=value,
                        raw_value=raw,
                        unit=fact_unit,
                        currency=parsed.meta.currency,
                        unit_scale=fact_scale,
                        period_label=period,
                        period_type=period_type,
                        comparison_kind=comparison_kind,
                        source=source,
                    )
                )
        return fallback

    def extract_nonrecurring_items(self, parsed: ParsedDocument) -> list[NonRecurringItem]:
        if parsed.meta.report_type == "business_update":
            return []
        page_text = {page.page: page.text for page in parsed.pages}
        items: list[NonRecurringItem] = []
        for table in parsed.tables:
            if not table.rows or "非经常性损益项目" not in normalize_text(table.rows[0][0] if table.rows[0] else ""):
                continue
            unit, scale = self._unit_from_text(page_text.get(table.page, ""), parsed.meta.unit_scale)
            for row_index, row in enumerate(table.rows):
                if not row:
                    continue
                label = (row[0] or "").strip()
                compact = normalize_text(label)
                if not compact or compact in {"项目", "非经常性损益项目"}:
                    continue
                numeric_cells = [
                    (str(cell).strip(), parsed_amount)
                    for cell in row[1:]
                    if (parsed_amount := parse_decimal(cell)) is not None
                ]
                periods: list[tuple[str, str, Decimal]] = []
                if parsed.meta.report_type == "q3" and numeric_cells:
                    periods.append((self._period_label(parsed.meta.report_year, "q3", "single_quarter"), *numeric_cells[0]))
                    if len(numeric_cells) > 1:
                        periods.append((self._period_label(parsed.meta.report_year, "q3", "cumulative"), *numeric_cells[1]))
                elif numeric_cells:
                    period_type = "annual" if parsed.meta.report_type == "annual" else "cumulative"
                    periods.append((self._period_label(parsed.meta.report_year, parsed.meta.report_type, period_type), *numeric_cells[0]))
                for period, raw_amount, amount in periods:
                    source = self._source(parsed, table, row_index, label, raw_amount)
                    items.append(
                        NonRecurringItem(
                            item_name=label,
                            amount=amount,
                            raw_amount=raw_amount,
                            unit=unit,
                            currency=parsed.meta.currency,
                            unit_scale=scale,
                            period_label=period,
                            is_total="合计" in compact,
                            source=source,
                        )
                    )
        deduplicated: list[NonRecurringItem] = []
        seen: set[tuple[str, str, int]] = set()
        for item in items:
            key = (normalize_text(item.item_name), str(item.normalized_amount), item.source.page)
            if key not in seen:
                seen.add(key)
                deduplicated.append(item)
        return deduplicated
