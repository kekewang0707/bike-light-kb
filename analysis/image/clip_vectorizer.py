"""CLIP 图片向量化器 — transformers 实现 + K-Means 风格聚类。

使用 HuggingFace transformers 加载 CLIP 模型，与项目共用 PyTorch 依赖。
生成 512 维图片向量，支持批量向量化和风格聚类分析。

M4/M5 边界：M4 只生成向量（存到 ImageAnalysis 表），不写 ChromaDB。
ChromaDB 的 Collection 创建和写入由 M5 统一负责。

模型懒加载：首次调用 vectorize() 时才加载模型。

Usage::

    from analysis.image.clip_vectorizer import CLIPVectorizer

    clip = CLIPVectorizer()
    vec = clip.vectorize("data/images/1/main_1.jpg")
    print(vec.shape)  # (512,)

    vectors = clip.vectorize_batch(["img1.jpg", "img2.jpg"])
    print(vectors.shape)  # (2, 512)

    clusters = clip.cluster_styles(vectors, n_clusters=5)
    for c in clusters.clusters:
        print(f"风格: {c.top_style_tags}, 数量: {c.size}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
from loguru import logger


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class StyleCluster:
    """单个风格簇。

    Attributes:
        indices:                  属于该簇的图片在原始列表中的索引。
        size:                     该簇包含的图片数量。
        top_style_tags:           该簇最常见风格标签（来自多模态描述）。
        representative_indices:   距离簇中心最近的 3 张图片索引。
    """

    indices: List[int] = field(default_factory=list)
    size: int = 0
    top_style_tags: List[str] = field(default_factory=list)
    representative_indices: List[int] = field(default_factory=list)


@dataclass
class StyleClusters:
    """K-Means 聚类结果。

    Attributes:
        n_clusters:  聚类数量。
        clusters:    各簇详情列表。
    """

    n_clusters: int = 0
    clusters: List[StyleCluster] = field(default_factory=list)


# ============================================================================
# CLIP 向量化器
# ============================================================================

class CLIPVectorizer:
    """CLIP 图片向量化器。

    使用 HuggingFace transformers.CLIPModel 加载模型。
    默认用 ViT-B/32（约 400MB，CPU 可接受）。
    后期可升级到 ViT-L/14（约 1.6GB，需 GPU）。

    Args:
        model_name: transformers 模型标识。
                    默认 "openai/clip-vit-base-patch32"（ViT-B/32）。
                    可选 "openai/clip-vit-large-patch14"（ViT-L/14）。
        device:     运行设备，"cpu" | "cuda" | None（None 表示自动检测）。
    """

    # 输入尺寸：CLIP 期望 224×224 像素
    IMAGE_SIZE = 224

    def __init__(
        self,
        model_name: str = "openai/clip-vit-base-patch32",
        device: Optional[str] = None,
    ):
        self.model_name = model_name
        self._device = device
        self._model = None
        self._processor = None

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def vectorize(self, image_path: str) -> np.ndarray:
        """生成单张图片的 CLIP 向量。

        Args:
            image_path: 图片本地路径。

        Returns:
            512 维 float32 向量。失败返回零向量（不抛异常）。
        """
        model, processor = self._get_model()
        if model is None:
            logger.warning(f"CLIP 模型不可用，返回零向量: {image_path}")
            return np.zeros(512, dtype=np.float32)

        try:
            return self._vectorize_one(image_path, model, processor)
        except Exception as e:
            logger.error(f"CLIP 向量化失败 [{image_path}]: {e}")
            return np.zeros(512, dtype=np.float32)

    def vectorize_batch(self, image_paths: List[str]) -> np.ndarray:
        """批量向量化。

        Args:
            image_paths: 图片路径列表。

        Returns:
            shape (N, 512) 的 float32 数组。
        """
        if not image_paths:
            return np.empty((0, 512), dtype=np.float32)

        model, processor = self._get_model()
        if model is None:
            logger.warning("CLIP 模型不可用，返回零向量")
            return np.zeros((len(image_paths), 512), dtype=np.float32)

        # 批处理（transformer 内部会自动分批）
        vectors = []
        for path in image_paths:
            try:
                v = self._vectorize_one(path, model, processor)
                vectors.append(v)
            except Exception as e:
                logger.error(f"CLIP 向量化失败 [{path}]: {e}")
                vectors.append(np.zeros(512, dtype=np.float32))

        return np.stack(vectors, axis=0)

    def cluster_styles(
        self,
        vectors: np.ndarray,
        n_clusters: int = 5,
        style_tags: Optional[List[List[str]]] = None,
        random_state: int = 42,
    ) -> StyleClusters:
        """K-Means 聚类，分析图片风格分布。

        Args:
            vectors:     shape (N, D) 的向量数组。
            n_clusters:  聚类数（默认 5）。
            style_tags:  每张图片的风格标签列表（来自多模态描述），
                         用于给每个簇标注最频繁关键词。
            random_state: 随机种子，保证可复现。

        Returns:
            StyleClusters 聚类结果。
        """
        n = len(vectors)
        if n == 0:
            return StyleClusters()

        # 动态调整聚类数
        actual_k = min(n_clusters, n)
        if actual_k < n_clusters:
            logger.info(f"样本量 ({n}) < 目标聚类数 ({n_clusters})，调整为 {actual_k}")

        try:
            from sklearn.cluster import KMeans
        except ImportError:
            logger.error("scikit-learn 未安装，无法聚类。pip install scikit-learn")
            return StyleClusters()

        # K-Means
        kmeans = KMeans(n_clusters=actual_k, random_state=random_state, n_init=10)
        labels = kmeans.fit_predict(vectors)

        # 找每个簇的中心
        centers = kmeans.cluster_centers_

        clusters: List[StyleCluster] = []
        for k in range(actual_k):
            # 该簇的所有索引
            idx = np.where(labels == k)[0]
            size = len(idx)

            # 距离中心最近的 3 张图片
            if len(idx) <= 3:
                rep_indices = idx.tolist()
            else:
                cluster_vecs = vectors[idx]
                center = centers[k]
                dists = np.linalg.norm(cluster_vecs - center, axis=1)
                top3_local = np.argsort(dists)[:3]
                rep_indices = idx[top3_local].tolist()

            # 该簇最常见的风格标签
            top_tags: List[str] = []
            if style_tags:
                tag_counts: dict = {}
                for i in idx:
                    for tag in (style_tags[i] if i < len(style_tags) else []):
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                top_tags = [
                    t for t, _ in sorted(tag_counts.items(), key=lambda x: -x[1])[:5]
                ]

            clusters.append(StyleCluster(
                indices=idx.tolist(),
                size=size,
                top_style_tags=top_tags,
                representative_indices=rep_indices,
            ))

        logger.info(
            f"CLIP 聚类完成: {actual_k} 个簇, "
            + ", ".join(f"簇{k}({c.size})" for k, c in enumerate(clusters))
        )

        return StyleClusters(n_clusters=actual_k, clusters=clusters)

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    def _get_model(self):
        """懒加载 CLIP 模型和处理器。"""
        if self._model is not None:
            return self._model, self._processor

        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor

            # 设备检测
            if self._device is None:
                self._device = (
                    "cuda" if torch.cuda.is_available() else
                    "mps" if torch.backends.mps.is_available() else
                    "cpu"
                )

            logger.info(f"加载 CLIP 模型: {self.model_name} (device={self._device})")

            self._model = CLIPModel.from_pretrained(self.model_name).to(self._device)
            self._model.eval()
            self._processor = CLIPProcessor.from_pretrained(self.model_name)

            logger.info("CLIP 模型加载完成")
            return self._model, self._processor

        except ImportError as e:
            logger.error(f"缺少依赖: {e}。请安装: pip install transformers torch")
        except Exception as e:
            logger.error(f"CLIP 模型加载失败: {e}")

        return None, None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _vectorize_one(
        self, image_path: str, model, processor
    ) -> np.ndarray:
        """向量化单张图片。"""
        from PIL import Image

        # 打开图片
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")

        img = Image.open(path).convert("RGB")

        # 预处理
        inputs = processor(
            images=img,
            return_tensors="pt",
        )
        # 移到设备
        if self._device != "cpu":
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

        # 推理
        import torch
        with torch.no_grad():
            outputs = model.get_image_features(**inputs)

        # transformers 新旧版本兼容：
        # 新版返回 BaseModelOutputWithPooling（含 .pooler_output）
        # 旧版直接返回 tensor
        if hasattr(outputs, "pooler_output"):
            image_features = outputs.pooler_output
        else:
            image_features = outputs

        # 归一化 + 转 numpy
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        vec = image_features.cpu().numpy().flatten()

        return vec.astype(np.float32)
