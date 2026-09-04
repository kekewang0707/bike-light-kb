# 淘宝商品详情接口 `specialUSPInfo` 及相关字段说明文档

> 数据来源：淘宝搜索/推荐流商品详情接口返回的JSON数据

---

## 📦 整体概览

这是一份淘宝商品详情页（来自搜索/推荐流）的JSON数据，包含商品的核心信息、价格、营销权益、榜单排名、用户评价等，用于前端渲染商品卡片。

---

## 📋 完整字段说明表

| 字段 | 值示例 | 类型 | 说明 |
|------|--------|------|------|
| `uniqpid` | `"642292607"` | String | 商品唯一标识ID，用于追踪和去重 |
| **`specialUSPInfo`** | 见下方 | Array | 🔥 **核心营销利益点**，展示品牌新客补贴等促销信息 |
| `priceShowWithIcon` | 见下方 | Object | **价格展示组件**，包含首单价、原价、价格颜色等UI配置 |
| `wfTwoLineTitle` | `"false"` | String | 标题是否双行展示（`true`/`false`） |
| `short_title_c2c` | `""` | String | C2C场景下的短标题（为空表示不展示） |
| `activityIdBase64` | `"MTA0MzgwMDUy"` | String | Base64编码的活动ID，解码后为 `104380052` |
| `summaryTipsColor` | `""` | String | 摘要提示文字的颜色（为空表示默认） |
| **`extraParams`** | 见下方 | Array | **透传参数**，用于埋点追踪、跳转携带等（如skuId、来源渠道） |
| **`imageInfo`** | 见下方 | Array | **商品主图**，展示在卡片上的第一张图 |
| `shopDiscountInfo` | `"品牌新客补贴，当日有效"` | String | **店铺优惠/权益信息的纯文本**，与`specialUSPInfo`中的`text`一致 |
| `nidlong` | `981461016089` | Number | 商品长ID，即 `itemId` |
| `title` | `"CBL1600PRO自行车骑行灯 远射205米磁吸 航空铝外壳"` | String | **商品标题** |
| **`iconUspSortInfo`** | 见下方 | Array | **图标型USP列表**，展示评价、榜单、热度等标签（每条带小图标） |
| `tItemType` | `"nt_auction_newindustry"` | String | 商品类型，`nt_auction_newindustry` 表示新版拍卖/行业商品 |
| **`utLogMap`** | 见下方 | Object | **算法/埋点日志参数**，包含召回策略、价格来源、曝光追踪等 |
| `auctionURL` | `"http://a.m.taobao.com/..."` | String | 商品详情页的H5链接 |
| **`trace`** | 见下方 | Object | **追踪参数**，包含spm和埋点信息，用于链路归因 |
| `itemCollect` | `"false"` | String | 当前用户是否已收藏该商品 |
| `price` | `"419.00"` | String | **商品原价**（划线价） |
| `realSales` | `"全网6000+人付款"` | String | **真实销量文案**，展示付款人数 |
| `sameCount` | `"7"` | String | 同款/相似商品数量 |

---

## 🔍 重点字段深度解析

### 1. `specialUSPInfo` — 核心营销利益点

```json
{
  "sort_type": "usp",                              // 类型：独特卖点
  "usp_code": "xinxiang:xinxiangDiscountSpeaker",  // 业务编码：心享体系下的新客补贴
  "text": "品牌新客补贴，当日有效",                  // 展示文案
  "receivedDefaultText": "恭喜领到优惠",             // 领取后文案
  "icon": "https://gw.alicdn.com/...png",          // 小图标
  "fieldTemplate": "discount",                     // 模板类型：折扣
  "textColor": "#FF294A"                           // 红色高亮
}
```

#### 子字段说明

| 子字段 | 说明 |
|--------|------|
| `sort_type` | 固定为 `"usp"`，标识这是一条独特卖点信息 |
| `usp_code` | 格式为 `业务域:具体活动ID`，`xinxiang` 是淘宝"心享"会员权益体系 |
| `text` | 用户看到的文案，这里是"品牌新客补贴，当日有效" |
| `receivedDefaultText` | 用户领取成功后的状态文案 |
| `icon` | 展示在文案前方的小图标URL |
| `iconWidth` / `iconHeight` | 图标尺寸（像素） |
| `fieldTemplate` | 前端渲染模板类型，`discount` 表示按优惠/折扣样式渲染 |
| `textColor` | 文案颜色，红色用于突出紧迫感 |

> 💡 **业务逻辑**：这是一个**限时、限新客**的促销卡片，未领取时显示"品牌新客补贴，当日有效"，点击/领取后变为"恭喜领到优惠"。适用于拉新场景，促进首单转化。

---

### 2. `iconUspSortInfo` — 强化信任的三个标签

| 序号 | `usp_code` | 展示文案 | 作用 |
|------|------------|----------|------|
| 1 | `numericalCommentInfo` | `"18人评价"质感很棒"` | 用户评价摘录，增强信任 |
| 2 | `tmRankInfo` | `"自行车灯热销榜·第2名"` | 榜单背书，带跳转链接 |
| 3 | `extend_source` | `"近7天3000+人逛过"` | 人气热度，制造从众效应 |

#### `tmRankInfo` 完整结构
```json
{
  "sort_type": "usp",
  "usp_code": "tmRankInfo",
  "text": "自行车灯热销榜·第2名",
  "textColor": "#B47738",
  "bgColor": "#FCF3E1",
  "rightIcon": "https://...24-24.png",
  "url": "https://pages-fast.m.taobao.com/...",  // 点击跳转榜单详情
  "rankType": "tmall",
  "borderRadius": "3"
}
```

> 💡 **三个标签的分工**：`specialUSPInfo` 侧重"价格优惠"，`iconUspSortInfo` 侧重"社会证明"，两者互补，共同提升转化率。

---

### 3. `priceShowWithIcon` — 价格展示组件

```json
{
  "price": "391.00",                    // 实际到手价（首单价）
  "originPrice": "￥419",               // 原价（划线价）
  "suffixText": "首单价",               // 后缀文案
  "priceColor": "#ff5000",              // 价格颜色（淘宝橙）
  "unit": "¥",                          // 货币符号
  "hiddenPriceUnderline": "true",       // 是否隐藏原价的下划线
  "showOriginPrice": "false",           // 是否展示原价
  "domClass": "umpcouponprice"          // DOM类名，用于样式控制
}
```

#### 页面渲染效果
> **¥391.00 首单价** ~~（原价 ￥419）~~

---

### 4. `extraParams` — 透传参数

| key | value | 说明 |
|-----|-------|------|
| `xxc` | `taobaoSearch` | 来源渠道：淘宝搜索 |
| `detailAlgoParam` | `%E8%87%AA%E8%A1%8C%E8%BD%A6%E7%81%AF` | URL编码的搜索词，解码为"自行车灯" |
| `skuId` | `6276213415832` | 当前SKU的ID |
| `skuPriceType` | `3` | SKU价格类型（3表示有营销价） |
| `upStreamPrice` | `39100` | 上游价格（单位：分），即391.00元 |
| `rankId` | `108508137` | 榜单ID |
| `mi_id` | `0000QKykhwoT...` | 用户/会话的埋点ID |

---

### 5. `utLogMap` — 算法埋点（最复杂）

这是**算法推荐和效果追踪**的核心字段，包含大量键值对：

| 关键键 | 值 | 说明 |
|--------|-----|------|
| `title` | `"algo"` | 标题来源为算法推荐 |
| `umpDirect` | `"true"` | 是否直接应用优惠 |
| `summary_price` | `"419.00"` | 摘要价格 |
| `saleField` | `"hot_sale_uv"` | 排序依据：热门销量UV |
| `saleText` | `"全网6000+人付款"` | 销量文案 |
| `provcity` | `"广东 深圳"` | 发货地 |
| `coup_info` | `"bCode:xinxianghongbao\|bCode:xyhf\|bCode:thbyf"` | 可用的优惠券代码 |
| `usp` | `"extend_source"` | 当前展示的USP类型 |
| `usp_icon_list` | `"usp:numericalCommentInfo,usp:tmRankInfo,usp:extend_source"` | 所有USP列表 |
| `umpLog` | `"sourceTypeKey:2_taobaoSearch;..."` | 完整优惠/营销日志 |
| `x_trace_type` | `"qy.lafei_hb.qy_xxzs.srp.search2detail"` | 流量链路类型 |
| `algo_select_log` | `"sku_rel_type:1;is_rel:1;mt:3;s:0.9953"` | 算法选择日志 |
| `list_param` | `"自行车灯_45_40ee3447890619b68cfa464448541e19"` | 搜索列表参数 |

> 💡 这个字段主要用于**记录本次展示的算法上下文**，便于后续分析点击转化率。

---

### 6. `trace` — 追踪参数

```json
{
  "spm-url": "a2141.7631557.0.0",     // 淘宝标准SPM追踪参数
  "item_id": "981461016089",          // 商品ID
  "utLogMap": {
    "item_price": "391",
    "list_param": "自行车灯_45_...",
    "x_biz": "item",
    "page": "1",
    "isP4p": "false"                  // 是否为付费推广商品
  }
}
```

---

## 📊 数据流向示意图

```
用户搜索"自行车灯" 
    → 算法召回该商品（itemId: 981461016089）
    → 判断用户为新客（xinxiang体系）
    → 组装数据：
        ├── specialUSPInfo → 展示"品牌新客补贴，当日有效"
        ├── iconUspSortInfo → 展示评价（18人推荐）+ 榜单（第2名）+ 热度（3000+人逛过）
        ├── priceShowWithIcon → 展示首单价 ¥391.00（原价 ¥419）
        └── utLogMap → 记录所有算法/埋点上下文
    → 前端渲染卡片
    → 用户点击 → 通过 extraParams + trace 进行归因
```

---

## ✅ 总结：各模块职责分工

| 模块 | 职责 | 目标用户 |
|------|------|----------|
| `specialUSPInfo` | 促销/优惠类利益点 | 价格敏感型用户 |
| `iconUspSortInfo` | 信任/热度类利益点 | 从众心理型用户 |
| `priceShowWithIcon` | 价格展示（含首单价/原价） | 所有用户 |
| `extraParams` + `trace` | 数据追踪与链路归因 | 数据分析侧 |
| `utLogMap` | 算法效果记录 | 算法/推荐侧 |
| `imageInfo` + `title` | 基础商品信息 | 所有用户 |

---

## 📎 附录：原始数据参考

```json
{
  "uniqpid": "642292607",
  "specialUSPInfo": [
    {
      "sort_type": "usp",
      "iconHeight": "20",
      "iconWidth": "20",
      "usp_code": "xinxiang:xinxiangDiscountSpeaker",
      "receivedDefaultText": "恭喜领到优惠",
      "icon": "https://gw.alicdn.com/imgextra/i3/O1CN01RPJMpG1I9o6J5dVYj_!!6000000000851-2-tps-80-80.png",
      "text": "品牌新客补贴，当日有效",
      "fieldTemplate": "discount",
      "textColor": "#FF294A"
    }
  ],
  "priceShowWithIcon": {
    "price": "391.00",
    "originPrice": "￥419",
    "suffixText": "首单价",
    "priceColor": "#ff5000",
    "unit": "¥"
  },
  "title": "CBL1600PRO自行车骑行灯 远射205米磁吸 航空铝外壳",
  "nidlong": 981461016089,
  "price": "419.00",
  "realSales": "全网6000+人付款"
}
```

---

> 📅 文档生成日期：2026-09-03
> 🔗 数据来源：淘宝搜索商品详情接口（taobaoSearch场景）
