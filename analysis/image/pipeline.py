"""ImageAnalysisPipeline — 端到端图片分析流水线。

编排 OCR → 多模态描述 → CLIP 向量化全流程，按图片类型分流处理。

职责：
1. 从 product_images 表查询待分析图片
2. 按图片类型分流：主图全量分析，详情图仅 OCR，晒图跳过
3. 批量去重（跳过已有分析记录的图片）
4. 执行分析并持久化到 image_analysis 表
5. 产出 ImageAnalysisReport

Session 管理：调用方传入 session，流水线不负责 session 生命周期。
参照 M3 中 ReviewAnalyzer / BatchRunner 的协作模式。

Usage::

    from models import get_session
    from analysis.image import ImageAnalysisPipeline

    session = next(get_session())
    try:
        pipeline = ImageAnalysisPipeline(session=session)
        report = await pipeline.analyze_all(limit=100)
        print(f"已分析: {report.analyzed}, OCR: {report.ocr_done}")
    finally:
        session.close()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ProductImage, ImageAnalysis, CRUDBase
from analysis.image.ocr import OCRExtractor, OCRResult
from analysis.image.multimodal import MultimodalDescriber, ImageDescription
from analysis.image.clip_vectorizer import CLIPVectorizer, StyleClusters


# ============================================================================
# 报告类型
# ============================================================================

@dataclass
class ImageAnalysisReport:
    """图片分析批次报告 — 与 M3 的 BatchResult 模式一致。

    Attributes:
        total_images:     本次扫描的图片总数。
        analyzed:         成功分析的图片数。
        ocr_done:         OCR 完成数。
        multimodal_done:  多模态描述完成数。
        clip_done:        CLIP 向量化完成数。
        failed:           失败数。
        skipped:          跳过数（已有分析结果或类型不处理）。
        style_clusters:   风格聚类结果（全量分析后生成）。
        duration_seconds: 总耗时。
        errors:           错误信息列表。
    """

    total_images: int = 0
    analyzed: int = 0
    ocr_done: int = 0
    multimodal_done: int = 0
    clip_done: int = 0
    failed: int = 0
    skipped: int = 0
    style_clusters: Optional[StyleClusters] = None
    duration_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)


# ============================================================================
# 图片分析流水线
# ============================================================================

class ImageAnalysisPipeline:
    """端到端图片分析流水线。

    编排 OCR、多模态描述、CLIP 向量化三步分析，
    按图片类型自动分流，结果写入 image_analysis 表。

    Args:
        ocr:        OCRExtractor 实例（不传则默认创建）。
        describer:  MultimodalDescriber 实例（不传则默认创建）。
        clip:       CLIPVectorizer 实例（不传则默认创建）。
        session:    SQLAlchemy 数据库会话。
    """

    # 图片根目录（与 ImageDownloader.save_dir 保持一致）
    IMAGE_BASE_DIR = Path("data/images")

    def __init__(
        self,
        ocr: Optional[OCRExtractor] = None,
        describer: Optional[MultimodalDescriber] = None,
        clip: Optional[CLIPVectorizer] = None,
        session: Optional[Session] = None,
    ):
        self.ocr = ocr or OCRExtractor()
        self.describer = describer or MultimodalDescriber()
        self.clip = clip or CLIPVectorizer()
        self.session = session

        self._image_crud = CRUDBase(ProductImage)
        self._analysis_crud = CRUDBase(ImageAnalysis)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def analyze_all(
        self,
        product_id: Optional[int] = None,
        limit: Optional[int] = None,
        concurrency: int = 3,
        do_cluster: bool = True,
    ) -> ImageAnalysisReport:
        """对全部未分析的图片执行分析。

        流程:
        1. 查询 product_images 表中无分析记录的图片
        2. 按图片类型分流
           - 主图 (main):  OCR + 多模态描述 + CLIP 向量化
           - 详情图 (detail): 仅 OCR
           - 其他:          跳过
        3. 批量分析 + 写入 image_analysis 表
        4. 可选：全品类 CLIP 聚类

        Args:
            product_id:  限定某商品的图片。不传则处理全部。
            limit:       最大处理数（用于测试）。不传则不限。
            concurrency: 多模态描述并发数。
            do_cluster:  分析完成后是否执行 CLIP 聚类。

        Returns:
            ImageAnalysisReport。
        """
        t0 = time.time()
        report = ImageAnalysisReport()

        if self.session is None:
            raise RuntimeError(
                "session 未设置。请传入 session=... 到 ImageAnalysisPipeline。"
            )

        # ---- Step 1: 查询待分析图片 ----
        all_images = self._query_pending_images(product_id, limit)
        if not all_images:
            logger.info("没有待分析的图片")
            report.duration_seconds = time.time() - t0
            return report

        report.total_images = len(all_images)

        # ---- Step 2: 类型分流 ----
        full_images = []   # 主图：全量分析
        ocr_only = []      # 详情图：仅 OCR

        for img in all_images:
            plan = self._analysis_plan(img)
            if "ocr" in plan or "multimodal" in plan or "clip" in plan:
                if "multimodal" in plan:
                    full_images.append(img)
                elif "ocr" in plan:
                    ocr_only.append(img)

        report.skipped = len(all_images) - len(full_images) - len(ocr_only)

        logger.info(
            f"图片分析: {len(full_images)} 张主图(全量分析), "
            f"{len(ocr_only)} 张详情图(仅OCR), "
            f"{report.skipped} 张跳过"
        )

        # ---- Step 3: 批量分析 ----

        # 3a. 详情图：仅 OCR（同步批量）
        if ocr_only:
            logger.info(f"开始 OCR 批量处理: {len(ocr_only)} 张详情图")
            for img in ocr_only:
                try:
                    analysis = self._run_ocr_only(img)
                    self._save_analysis(img, analysis)
                    report.ocr_done += 1
                    report.analyzed += 1
                except Exception as e:
                    logger.error(f"详情图分析失败 [image_id={img.id}]: {e}")
                    report.failed += 1
                    report.errors.append(f"ocr[{img.id}]: {e}")

        # 3b. 主图：OCR + 多模态 + CLIP
        if full_images:
            logger.info(
                f"开始主图批量处理: {len(full_images)} 张, concurrency={concurrency}"
            )

            # 收集图片绝对路径
            full_paths = [
                self._resolve_path(img) for img in full_images
                if img.local_path
            ]

            # OCR（同步批量 — 本地模型，不需要并发控制）
            ocr_results: List[Optional[OCRResult]] = []
            for img in full_images:
                abs_path = self._resolve_path(img)
                if abs_path:
                    ocr_results.append(self.ocr.extract(abs_path))
                else:
                    ocr_results.append(None)

            # 多模态描述（异步并发 — API 调用）
            desc_results = await self.describer.describe_batch(
                full_paths, concurrency=concurrency
            )

            # CLIP 向量化（同步批量 — 本地模型）
            clip_vectors = self.clip.vectorize_batch(full_paths)

            # 汇总写入
            for i, img in enumerate(full_images):
                try:
                    ocr_result = ocr_results[i] if i < len(ocr_results) else None
                    desc = desc_results[i] if i < len(desc_results) else None
                    clip_vec = clip_vectors[i] if i < len(clip_vectors) else None

                    analysis = self._build_analysis(
                        img,
                        ocr_result=ocr_result,
                        description=desc,
                        clip_vec=clip_vec,
                    )
                    self._save_analysis(img, analysis)
                    report.analyzed += 1

                    if ocr_result is not None:
                        report.ocr_done += 1
                    if desc is not None:
                        report.multimodal_done += 1
                    if clip_vec is not None and clip_vec.any():
                        report.clip_done += 1

                except Exception as e:
                    logger.error(f"主图分析失败 [image_id={img.id}]: {e}")
                    report.failed += 1
                    report.errors.append(f"full[{img.id}]: {e}")

        # ---- Step 4: 风格聚类 ----
        if do_cluster:
            try:
                report.style_clusters = await self.recluster()
            except Exception as e:
                logger.error(f"风格聚类失败: {e}")
                report.errors.append(f"recluster: {e}")

        report.duration_seconds = round(time.time() - t0, 1)
        logger.info(
            f"图片分析完成 [{report.duration_seconds}s]: "
            f"分析 {report.analyzed}/{report.total_images}, "
            f"OCR {report.ocr_done}, 多模态 {report.multimodal_done}, "
            f"CLIP {report.clip_done}, 失败 {report.failed}, 跳过 {report.skipped}"
        )

        return report

    async def analyze_single(
        self, image: ProductImage
    ) -> Optional[ImageAnalysis]:
        """分析单张图片。按 image_type 自动分流。

        与 M3 的 ReviewAnalyzer.analyze_single() 返回模式一致：
        返回 ORM 实例（未写入 DB），失败返回 None。

        Args:
            image: ProductImage ORM 实例。

        Returns:
            ImageAnalysis ORM 实例，或 None。
        """
        plan = self._analysis_plan(image)

        if not plan:
            logger.debug(f"图片 {image.id} (type={image.image_type}): 跳过")
            return None

        abs_path = self._resolve_path(image)
        if not abs_path:
            logger.warning(f"图片 {image.id} 无 local_path，跳过")
            return None

        ocr_result = None
        description = None
        clip_vec = None

        if "ocr" in plan:
            ocr_result = self.ocr.extract(abs_path)

        if "multimodal" in plan:
            description = await self.describer.describe(abs_path)

        if "clip" in plan:
            clip_vec = self.clip.vectorize(abs_path)

        return self._build_analysis(
            image,
            ocr_result=ocr_result,
            description=description,
            clip_vec=clip_vec,
        )

    async def recluster(self) -> StyleClusters:
        """重新聚类（新图片入库后调用）。

        从已分析的主图中读取 CLIP 向量和风格标签，
        执行 K-Means 聚类。

        Returns:
            StyleClusters 聚类结果。
        """
        if self.session is None:
            raise RuntimeError("session 未设置")

        # 查询已有分析结果的图片及其分析数据
        rows = self.session.execute(
            select(ImageAnalysis, ProductImage).join(
                ProductImage, ImageAnalysis.image_id == ProductImage.id
            ).where(
                ProductImage.image_type == "main",
                ImageAnalysis.clip_vector_id.isnot(None),
            )
        ).all()

        if not rows:
            logger.info("没有可聚类的主图数据")
            return StyleClusters()

        # 收集向量（从 image_analysis 的 clip_vector_id 读取）
        # 注：当前阶段 clip_vector_id 只存占位符，实际向量在内存中
        # 如需从 DB 恢复向量，可在 M5 中从 ChromaDB 读取
        logger.warning(
            "recluster() 需要 CLIP 向量。"
            "当前实现从 image_analysis 表读取，但向量可能尚未持久化到 ChromaDB。"
            "建议在 analyze_all() 后直接使用返回的 StyleClusters。"
        )

        # 暂不实现从 DB 恢复向量的逻辑，返回空结果
        return StyleClusters()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _resolve_path(self, image: ProductImage) -> Optional[str]:
        """将 ProductImage.local_path 解析为绝对文件路径。

        local_path 是相对于 IMAGE_BASE_DIR 的路径（由 ImageDownloader 生成），
        需要拼接为绝对路径后才能传给 OCR/CLIP/VL。

        Returns:
            绝对路径字符串。如果 local_path 为空或文件不存在，返回 None。
        """
        if not image.local_path:
            return None

        full_path = self.IMAGE_BASE_DIR / image.local_path
        if not full_path.exists():
            logger.debug(f"图片文件不存在: {full_path}")
            return None

        return str(full_path.resolve())

    def _analysis_plan(self, image: ProductImage) -> List[str]:
        """根据图片类型返回分析计划。

        Args:
            image: ProductImage 实例。

        Returns:
            ["ocr", "multimodal", "clip"] 的子集。

        规则:
        - main:        ["ocr", "multimodal", "clip"]
        - detail:      ["ocr"]
        - video_cover: ["ocr"]
        - review:      []（跳过）
        - 其他:        ["ocr"]
        """
        img_type = (image.image_type or "").strip().lower()

        if img_type == "main":
            return ["ocr", "multimodal", "clip"]
        elif img_type in ("detail", "video_cover"):
            return ["ocr"]
        elif img_type == "review":
            return []
        else:
            # 未知类型，保守处理：仅 OCR
            logger.debug(f"未知图片类型 '{img_type}'，默认仅 OCR")
            return ["ocr"]

    def _query_pending_images(
        self,
        product_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ProductImage]:
        """查询 product_images 表中无分析记录的图片。

        Args:
            product_id: 限定某商品。
            limit:      最大返回数。

        Returns:
            ProductImage 列表。
        """
        # 查询所有已分析的 image_id
        analyzed_subq = select(ImageAnalysis.image_id)

        q = select(ProductImage).where(
            ProductImage.id.not_in(analyzed_subq)
        )

        if product_id is not None:
            q = q.where(ProductImage.product_id == product_id)

        q = q.order_by(ProductImage.sort_order)

        if limit is not None:
            q = q.limit(limit)

        rows = self.session.execute(q).scalars().all()
        return list(rows)

    def _build_analysis(
        self,
        image: ProductImage,
        ocr_result: Optional[OCRResult] = None,
        description: Optional[ImageDescription] = None,
        clip_vec: Optional["np.ndarray"] = None,
    ) -> ImageAnalysis:
        """构建 ImageAnalysis ORM 实例。

        Args:
            image:        关联的 ProductImage。
            ocr_result:   OCR 提取结果。
            description:  多模态描述结果。
            clip_vec:     CLIP 向量。

        Returns:
            ImageAnalysis 实例（未写入 DB）。
        """
        from datetime import datetime, timezone

        # CLIP 向量 ID（占位符，M5 会替换为真正的 ChromaDB ID）
        clip_vector_id = None
        if clip_vec is not None and clip_vec.any():
            clip_vector_id = f"clip_{image.id}"

        # 风格标签提取
        style_tags = None
        if description and description.style:
            # 从逗号分隔的风格描述中提取标签
            style_tags = [
                tag.strip()
                for tag in description.style.replace("/", ",").split(",")
                if tag.strip()
            ]

        # 综合描述文本
        desc_text = None
        if description:
            parts = []
            for key in ("appearance", "mount_type", "scene", "style", "text_overlay"):
                val = getattr(description, key, "")
                if val:
                    parts.append(f"{key}: {val}")
            desc_text = "; ".join(parts) if parts else None

        return ImageAnalysis(
            image_id=image.id,
            ocr_text=ocr_result.full_text if ocr_result else None,
            description=desc_text,
            style_tags=style_tags,
            clip_vector_id=clip_vector_id,
            analyzed_at=datetime.now(timezone.utc),
        )

    def _save_analysis(
        self, image: ProductImage, analysis: ImageAnalysis
    ):
        """将分析结果持久化到 image_analysis 表。

        按 image_id 去重 upsert，支持对同一图片重复分析。

        Args:
            image:    关联的 ProductImage。
            analysis: 待保存的 ImageAnalysis 实例。
        """
        if self.session is None:
            raise RuntimeError("session 未设置")

        self._analysis_crud.upsert_by(
            self.session,
            filters={"image_id": image.id},
            updates={
                "ocr_text": analysis.ocr_text,
                "description": analysis.description,
                "style_tags": analysis.style_tags,
                "clip_vector_id": analysis.clip_vector_id,
                "analyzed_at": analysis.analyzed_at,
            },
        )

    def _run_ocr_only(self, image: ProductImage) -> ImageAnalysis:
        """对详情图执行仅 OCR 分析（同步）。

        Args:
            image: ProductImage 实例。

        Returns:
            ImageAnalysis 实例。
        """
        ocr_result = None
        abs_path = self._resolve_path(image)
        if abs_path:
            ocr_result = self.ocr.extract(abs_path)

        return self._build_analysis(image, ocr_result=ocr_result)
