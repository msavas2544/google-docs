"""Turn data into a formatted Google Doc: a heading, a summary and a table.

    python examples/weekly_report.py
"""

from google_docs import Content, DocsClient, OAuthCredentials, bold
from google_docs.cli import DEFAULT_TOKEN

sales = [("North", 1_240_000, 0.22), ("South", 810_000, 0.09), ("West", 655_000, -0.03)]
total = sum(revenue for _, revenue, _ in sales)

report = (
    Content()
    .title("Weekly sales")
    .paragraph("Total revenue: ", bold(f"${total:,.0f}"), ".")
    .heading("By region", 2)
    .table([("Region", "Revenue", "Growth"),
            *[(name, f"${rev:,.0f}", f"{growth:+.0%}") for name, rev, growth in sales]])
    .heading("Notes", 2)
    .bullets([f"{name} is {'up' if g > 0 else 'down'}" for name, _, g in sales])
)

with DocsClient(OAuthCredentials.load(DEFAULT_TOKEN)) as docs:
    doc = docs.create("Weekly sales", report)
    print(doc.url)
