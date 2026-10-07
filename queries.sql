-- 主清单：按用户口径，单条前台“已拼/已抢”精确件数严格大于10。
-- 原库 eligible_gt10 是封存时的旧口径，展示重新派生；每张卡片独立保留。
SELECT r.observed_from, o.view_order, o.title, o.sales_raw, o.price_raw,
       o.goods_id, o.goods_url, t.status AS image_status, t.asset_sha256
FROM observations AS o
JOIN runs AS r ON r.run_id=o.run_id
LEFT JOIN image_tasks AS t ON t.observation_id=o.observation_id
WHERE o.run_id=(SELECT run_id FROM runs ORDER BY observed_to_epoch DESC LIMIT 1)
  AND o.sales_label IN ('已拼', '已抢')
  AND o.sales_precision='exact_display' AND o.sales_unit='件' AND o.sales_value>10
ORDER BY o.view_order;

-- 同标题候选组的各条卡片。这里不做销量求和。
SELECT g.group_code, g.relationship, o.view_order, o.title,
       o.sales_raw, o.price_raw, gm.is_trigger
FROM candidate_groups AS g
JOIN group_members AS gm ON gm.group_id=g.group_id AND gm.run_id=g.run_id
JOIN observations AS o ON o.observation_id=gm.observation_id
ORDER BY g.run_id, g.first_view_order, o.view_order;

-- 缺图与历史失败：状态保留，不自动重试。
SELECT status, COUNT(*) AS card_count FROM image_tasks GROUP BY status;

-- 图片库需先通过 ATTACH DATABASE 'images.sqlite3' AS images 连接。
SELECT sha256, mime, byte_count FROM images.assets ORDER BY sha256;
