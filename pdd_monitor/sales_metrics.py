"""Current display-sales aliases; immutable capture fields remain untouched."""

SALES_METRIC_LABELS = ('已拼', '已抢')
SALES_METRIC_VERSION = 'yipin_yiqiang_exact_items_v2'


def same_sales_metric(left, right):
    """Alias only item counts; other labels must still match literally."""
    if not left.get('sales_label') or not right.get('sales_label'):
        return False
    return (left['sales_label'] == right['sales_label'] or
            (left['sales_label'] in SALES_METRIC_LABELS and right['sales_label'] in SALES_METRIC_LABELS
             and left.get('sales_unit') == right.get('sales_unit') == '件'))
