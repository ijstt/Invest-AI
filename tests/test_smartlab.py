"""Тесты парсера фундаменталки smart-lab (чистые функции; сеть не нужна)."""

from __future__ import annotations

from geoanalytics.connectors.smartlab import _is_year, _parse_value, parse_financials

# Мини-таблица в формате smart-lab: год-строка + строки с атрибутом field, масштабными
# единицами в <span>, пустыми ячейками за пропущенные годы и хвостовой колонкой LTM.
_HTML = """
<table>
  <tr><th>Эмитент (X): годовая отчетность МСФО</th></tr>
  <tr><th></th><th></th><td class="chartrow"></td>
      <td>2022</td><td>2023</td><td>2024</td><td></td><td>LTM</td></tr>
  <tr><th></th><th></th><td class="chartrow"></td>
      <td>RUB</td><td>RUB</td><td>RUB</td><td></td><td>RUB</td></tr>
  <tr field="revenue"><th><a>Выручка</a>, <span>млрд руб</span></th><th></th>
      <td class="chartrow"></td><td>1 000</td><td>1 200</td>
      <td>1 500</td><td></td><td>1 600</td></tr>
  <tr field="net_income"><th><a>Чистая прибыль</a>, <span>млрд руб</span></th><th></th>
      <td class="chartrow"></td><td>100</td><td>120</td><td>150</td><td></td><td>160</td></tr>
  <tr field="roe"><th><a>ROE</a>, <span>%</span></th><th></th>
      <td class="chartrow"></td><td>24.2%</td><td>20.0%</td><td></td><td></td><td>22%</td></tr>
  <tr field="p_e"><th><a>P/E</a></th><th></th>
      <td class="chartrow"></td><td>5.28</td><td>4.0</td><td>3.5</td><td></td><td>3.4</td></tr>
  <tr field="dividend"><th><a>Дивиденд</a>, <span>руб/акцию</span></th><th></th>
      <td class="chartrow"></td><td>25</td><td>33.3</td><td>34.84</td><td></td><td>37</td></tr>
  <tr field="unknown_metric"><th><a>X</a></th><th></th>
      <td class="chartrow"></td><td>1</td><td>2</td><td>3</td><td></td><td>4</td></tr>
</table>
"""


def _facts_by(metric, facts):
    return {f.period: f for f in facts if f.metric == metric}


class TestParseValue:
    def test_money_scaled_to_rub(self):
        assert _parse_value("1 000", "money", "млрд руб") == (1_000e9, "RUB")
        assert _parse_value("150", "money", "млн руб") == (150e6, "RUB")

    def test_pct_keeps_percent_number(self):
        assert _parse_value("24.2%", "pct", "%") == (24.2, "pct")

    def test_ratio_coefficient(self):
        assert _parse_value("5.28", "ratio", "") == (5.28, "ratio")

    def test_share_currency(self):
        assert _parse_value("34.84", "share", "руб/акцию") == (34.84, "RUB")

    def test_empty_and_dash(self):
        assert _parse_value("", "money", "млрд руб") is None
        assert _parse_value("—", "pct", "%") is None


class TestIsYear:
    def test_years_and_non_years(self):
        assert _is_year("2024")
        assert _is_year("1999")
        assert not _is_year("LTM")
        assert not _is_year("")
        assert not _is_year("24")


class TestParseFinancials:
    def test_revenue_multi_period_rub(self):
        facts = parse_financials(_HTML)
        rev = _facts_by("revenue", facts)
        assert set(rev) == {"2022", "2023", "2024"}            # LTM-колонка отброшена
        assert rev["2024"].value == 1_500e9
        assert rev["2024"].unit == "RUB"

    def test_roe_pct_and_missing_year_skipped(self):
        facts = parse_financials(_HTML)
        roe = _facts_by("roe", facts)
        assert roe["2022"].value == 24.2 and roe["2022"].unit == "pct"
        assert "2024" not in roe                               # пустая ячейка пропущена

    def test_pe_ratio_and_dividend_share(self):
        facts = parse_financials(_HTML)
        assert _facts_by("pe", facts)["2022"].value == 5.28
        assert _facts_by("pe", facts)["2022"].unit == "ratio"
        assert _facts_by("dividend", facts)["2024"].value == 34.84

    def test_derived_net_margin(self):
        facts = parse_financials(_HTML)
        nm = _facts_by("net_margin", facts)
        assert nm["2024"].value == 10.0 and nm["2024"].unit == "pct"   # 150/1500*100

    def test_unknown_field_ignored(self):
        facts = parse_financials(_HTML)
        assert all(f.metric != "unknown_metric" for f in facts)

    def test_empty_html(self):
        assert parse_financials("<html><body>пусто</body></html>") == []
