from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


@dataclass(slots=True)
class Settings:
    project_root: Path
    output_dir: Path
    data_roots: list[Path]
    external_evidence_dir: Path
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_temperature: float
    deepseek_timeout_seconds: int
    deepseek_max_tokens: int
    ocr_provider: str
    ocr_mode: str
    ocr_dpi: int
    ocr_max_pages_per_document: int
    aistudio_api_url: str
    aistudio_access_token: str
    aistudio_auth_scheme: str
    aistudio_request_style: str
    aistudio_model: str
    aistudio_poll_interval_seconds: float
    aistudio_poll_timeout_seconds: int
    aistudio_timeout_seconds: int
    baidu_ocr_api_key: str
    baidu_ocr_secret_key: str
    baidu_ocr_endpoint: str
    baidu_ocr_timeout_seconds: int

    @classmethod
    def load(cls, env_path: Path | None = None) -> "Settings":
        load_dotenv(env_path or PROJECT_ROOT / ".env")
        output_raw = os.getenv("FIN_AGENT_OUTPUT_DIR", "outputs")
        output_dir = Path(output_raw)
        if not output_dir.is_absolute():
            output_dir = PROJECT_ROOT / output_dir

        roots_raw = os.getenv("FIN_AGENT_DATA_ROOTS", "../财报分析;../年报")
        roots: list[Path] = []
        for item in roots_raw.split(";"):
            item = item.strip()
            if not item:
                continue
            root = Path(item)
            if not root.is_absolute():
                root = (PROJECT_ROOT / root).resolve()
            roots.append(root)
        managed_report_root = (PROJECT_ROOT / "财报数据").resolve()
        if all(root.resolve() != managed_report_root for root in roots):
            roots.insert(0, managed_report_root)

        return cls(
            project_root=PROJECT_ROOT,
            output_dir=output_dir.resolve(),
            data_roots=roots,
            external_evidence_dir=(
                Path(os.getenv("FIN_AGENT_EXTERNAL_EVIDENCE_DIR", "external_evidence"))
                if Path(os.getenv("FIN_AGENT_EXTERNAL_EVIDENCE_DIR", "external_evidence")).is_absolute()
                else PROJECT_ROOT / os.getenv("FIN_AGENT_EXTERNAL_EVIDENCE_DIR", "external_evidence")
            ).resolve(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip(),
            deepseek_temperature=_float("DEEPSEEK_TEMPERATURE", 0.0),
            deepseek_timeout_seconds=_int("DEEPSEEK_TIMEOUT_SECONDS", 180),
            deepseek_max_tokens=_int("DEEPSEEK_MAX_TOKENS", 5000),
            ocr_provider=os.getenv("OCR_PROVIDER", "none").strip().lower(),
            ocr_mode=os.getenv("OCR_MODE", "auto").strip().lower(),
            ocr_dpi=_int("OCR_DPI", 220),
            ocr_max_pages_per_document=_int("OCR_MAX_PAGES_PER_DOCUMENT", 30),
            aistudio_api_url=os.getenv("AISTUDIO_API_URL", "").strip(),
            aistudio_access_token=os.getenv("AISTUDIO_ACCESS_TOKEN", "").strip(),
            aistudio_auth_scheme=os.getenv("AISTUDIO_AUTH_SCHEME", "bearer").strip().lower(),
            aistudio_request_style=os.getenv("AISTUDIO_REQUEST_STYLE", "paddleocr_job").strip().lower(),
            aistudio_model=os.getenv("AISTUDIO_MODEL", "PaddleOCR-VL-1.6").strip(),
            aistudio_poll_interval_seconds=_float("AISTUDIO_POLL_INTERVAL_SECONDS", 5.0),
            aistudio_poll_timeout_seconds=_int("AISTUDIO_POLL_TIMEOUT_SECONDS", 1800),
            aistudio_timeout_seconds=_int("AISTUDIO_TIMEOUT_SECONDS", 180),
            baidu_ocr_api_key=os.getenv("BAIDU_OCR_API_KEY", "").strip(),
            baidu_ocr_secret_key=os.getenv("BAIDU_OCR_SECRET_KEY", "").strip(),
            baidu_ocr_endpoint=os.getenv(
                "BAIDU_OCR_ENDPOINT",
                "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic",
            ).strip(),
            baidu_ocr_timeout_seconds=_int("BAIDU_OCR_TIMEOUT_SECONDS", 120),
        )

    def redacted(self) -> dict:
        data = asdict(self)
        data["project_root"] = str(self.project_root)
        data["output_dir"] = str(self.output_dir)
        data["data_roots"] = [str(path) for path in self.data_roots]
        data["external_evidence_dir"] = str(self.external_evidence_dir)
        for key in (
            "deepseek_api_key",
            "aistudio_access_token",
            "baidu_ocr_api_key",
            "baidu_ocr_secret_key",
        ):
            data[key] = "configured" if data[key] else "missing"
        return data

    def llm_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    def ocr_configured(self) -> bool:
        if self.ocr_provider == "aistudio":
            if self.aistudio_auth_scheme == "none":
                return bool(self.aistudio_api_url)
            return bool(self.aistudio_api_url and self.aistudio_access_token)
        if self.ocr_provider == "baidu_cloud":
            return bool(self.baidu_ocr_api_key and self.baidu_ocr_secret_key)
        return False
