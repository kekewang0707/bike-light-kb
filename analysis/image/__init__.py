"""M4 — 图片分析管道 (Image Analysis Pipeline)。

对商品图片进行三层分析：

1. OCR 层:  PaddleOCR/EasyOCR 提取图片中的文字（卖点标签、规格标注）
2. 语义层: Qwen-VL 多模态模型生成图片综合描述
3. 向量层: CLIP (transformers) 生成 512 维图片向量

分析结果写入 image_analysis 表，后续由 M5 同步到 ChromaDB。

主要入口类::

    from analysis.image import (
        OCRExtractor,
        MultimodalDescriber,
        CLIPVectorizer,
        ImageAnalysisPipeline,
    )

    pipeline = ImageAnalysisPipeline(session=session)
    report = await pipeline.analyze_all()
"""

from analysis.image.ocr import OCRExtractor, OCRResult
from analysis.image.multimodal import MultimodalDescriber, ImageDescription
from analysis.image.clip_vectorizer import CLIPVectorizer, StyleClusters, StyleCluster
from analysis.image.pipeline import ImageAnalysisPipeline, ImageAnalysisReport

__all__ = [
    # ---- OCR ----
    "OCRExtractor",
    "OCRResult",
    # ---- Multimodal ----
    "MultimodalDescriber",
    "ImageDescription",
    # ---- CLIP ----
    "CLIPVectorizer",
    "StyleClusters",
    "StyleCluster",
    # ---- Pipeline ----
    "ImageAnalysisPipeline",
    "ImageAnalysisReport",
]
