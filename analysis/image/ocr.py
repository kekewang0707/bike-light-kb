"""OCR 文字提取器 — 双后端（PaddleOCR / EasyOCR）。

从商品图片中提取文字信息，识别卖点标签和规格标注。

后端选择策略：
- 优先 PaddleOCR（中文准确率最高）
- macOS 或 PaddleOCR 不可用时自动降级 EasyOCR
- 两者返回统一的 OCRResult，调用方无感知

模型懒加载：首次调用 extract() 时才初始化 OCR 模型。

Usage::

    from analysis.image.ocr import OCRExtractor

    ocr = OCRExtractor()                # 默认 PaddleOCR，不可用则降级
    result = ocr.extract("data/images/1/main_1.jpg")
    print(result.full_text)
    print(result.key_phrases)

    results = ocr.extract_batch(["img1.jpg", "img2.jpg"])
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class OCRResult:
    """单张图片的 OCR 提取结果。

    Attributes:
        full_text:    所有识别文本（空格拼接），用于全文搜索。
        text_blocks:  带位置和置信度的文本块列表。
        key_phrases:  过滤出的可能是卖点标签的短文本块，
                      如 ["800流明", "IPX6防水", "USB-C充电"]。
    """

    full_text: str = ""
    text_blocks: List[dict] = field(default_factory=list)
    key_phrases: List[str] = field(default_factory=list)


# ============================================================================
# OCR 提取器
# ============================================================================

class OCRExtractor:
    """商品图片 OCR 文字提取器。

    双后端实现：
    - PaddleOCR: 中文场景最优，但 macOS 安装较困难
    - EasyOCR:   跨平台兼容性好，安装简单

    Args:
        backend:  首选后端 "paddleocr" | "easyocr"（默认 paddleocr）
        lang:     识别语言列表。PaddleOCR 用 "ch"，EasyOCR 用 ["ch_sim", "en"]
        use_gpu:  是否使用 GPU（默认 False，CPU 即可满足 < 1000 张的规模）
    """

    # 卖点标签匹配模式：数字+单位、规格关键词
    KEY_PHRASE_PATTERNS = [
        re.compile(
            r"(\d+\s*(?:流明|lumen|lm|lux|瓦|w|毫安|mah|安时|ah|"
            r"伏|v|米|m|克|g|千克|kg|小时|小时|分钟|min))",
            re.IGNORECASE,
        ),
        re.compile(
            r"(IP\d{2}|ip\d{2}|IPX\d|ipx\d)"  # 防水等级
        ),
        re.compile(
            r"(USB[- ]?[AC]|Type[- ]?[AC]|Micro[- ]?USB|Lightning)"  # 充电接口
        ),
    ]

    # 被视为规格关键词的触发词
    SPEC_KEYWORDS = [
        "流明", "防水", "电池", "充电", "续航", "安装", "重量",
        "材质", "铝合金", "ABS", "LED", "COB", "T6", "L2",
        "快拆", "车把", "座管", "头盔", "USB", "Type-C",
        "lumen", "lumens", "lm", "IPX", "IP",
        "mAh", "mAH", "小时", "分钟",
    ]

    def __init__(
        self,
        backend: str = "paddleocr",
        lang=None,
        use_gpu: bool = False,
    ):
        self._preferred_backend = backend.lower()
        self._use_gpu = use_gpu
        self._lang = lang  # None 表示用默认值
        self._model = None
        self._active_backend: Optional[str] = None

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def extract(self, image_path: str) -> OCRResult:
        """提取单张图片的文字。

        Args:
            image_path: 图片本地路径。

        Returns:
            OCRResult，识别失败时返回空结果（不抛异常）。
        """
        model = self._get_model()
        if model is None:
            logger.warning(f"OCR 模型不可用，跳过: {image_path}")
            return OCRResult()

        try:
            if self._active_backend == "paddleocr":
                return self._extract_paddleocr(model, image_path)
            else:
                return self._extract_easyocr(model, image_path)
        except Exception as e:
            logger.error(f"OCR 提取失败 [{image_path}]: {e}")
            return OCRResult()

    def extract_batch(self, image_paths: List[str]) -> List[OCRResult]:
        """批量 OCR，顺序处理。

        Args:
            image_paths: 图片路径列表。

        Returns:
            与输入顺序对应的 OCRResult 列表。
        """
        return [self.extract(p) for p in image_paths]

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    def _get_model(self):
        """懒加载 OCR 模型。"""
        if self._model is not None:
            return self._model

        if self._preferred_backend == "paddleocr":
            model = self._try_load_paddleocr()
            if model is not None:
                self._active_backend = "paddleocr"
                self._model = model
                logger.info("OCR 后端: PaddleOCR")
                return self._model

            logger.warning(
                "PaddleOCR 不可用，降级到 EasyOCR。"
                "如需使用 PaddleOCR，请执行: pip install paddlepaddle paddleocr"
            )

        # 降级到 EasyOCR
        model = self._try_load_easyocr()
        if model is not None:
            self._active_backend = "easyocr"
            self._model = model
            logger.info("OCR 后端: EasyOCR")
            return self._model

        logger.error("所有 OCR 后端均不可用。请安装: pip install easyocr")
        return None

    @staticmethod
    def _try_load_paddleocr():
        """尝试加载 PaddleOCR。"""
        try:
            from paddleocr import PaddleOCR
            return PaddleOCR(lang="ch", use_gpu=False, show_log=False)
        except ImportError:
            logger.debug("PaddleOCR 未安装")
        except Exception as e:
            logger.warning(f"PaddleOCR 加载失败: {e}")
        return None

    @staticmethod
    def _try_load_easyocr():
        """尝试加载 EasyOCR。"""
        try:
            import easyocr
            return easyocr.Reader(["ch_sim", "en"], gpu=False)
        except ImportError:
            logger.debug("EasyOCR 未安装")
        except Exception as e:
            logger.warning(f"EasyOCR 加载失败: {e}")
        return None

    # ------------------------------------------------------------------
    # 后端适配
    # ------------------------------------------------------------------

    def _extract_paddleocr(self, model, image_path: str) -> OCRResult:
        """PaddleOCR 后端 → 统一 OCRResult。"""
        raw = model.ocr(image_path)

        # PaddleOCR 返回: [[[bbox], (text, confidence)], ...]
        # 如果图片中无文字，可能返回 None 或空列表
        if not raw or not raw[0]:
            return OCRResult()

        text_blocks = []
        key_phrases = []

        for line in raw[0]:
            bbox, (text, confidence) = line
            text = text.strip()
            if not text:
                continue

            block = {
                "text": text,
                "bbox": bbox,
                "confidence": round(confidence, 4),
            }
            text_blocks.append(block)

            if self._is_key_phrase(text):
                key_phrases.append(text)

        full_text = " ".join(b["text"] for b in text_blocks)

        return OCRResult(
            full_text=full_text,
            text_blocks=text_blocks,
            key_phrases=key_phrases,
        )

    def _extract_easyocr(self, model, image_path: str) -> OCRResult:
        """EasyOCR 后端 → 统一 OCRResult。"""
        raw = model.readtext(image_path)

        # EasyOCR 返回: [(bbox, text, confidence), ...]
        text_blocks = []
        key_phrases = []

        for bbox, text, confidence in raw:
            text = text.strip()
            if not text:
                continue

            block = {
                "text": text,
                "bbox": bbox,
                "confidence": round(confidence, 4),
            }
            text_blocks.append(block)

            if self._is_key_phrase(text):
                key_phrases.append(text)

        full_text = " ".join(b["text"] for b in text_blocks)

        return OCRResult(
            full_text=full_text,
            text_blocks=text_blocks,
            key_phrases=key_phrases,
        )

    # ------------------------------------------------------------------
    # 关键词过滤
    # ------------------------------------------------------------------

    def _is_key_phrase(self, text: str) -> bool:
        """判断文本块是否为卖点/规格标签。

        规则：
        1. 匹配数字+单位模式（如 "800流明"）
        2. 匹配防水等级模式（如 "IPX6"）
        3. 包含规格关键词且长度 ≤ 20 字符
        """
        if len(text) > 20:
            return False

        for pattern in self.KEY_PHRASE_PATTERNS:
            if pattern.search(text):
                return True

        for kw in self.SPEC_KEYWORDS:
            if kw.lower() in text.lower():
                return True

        return False
