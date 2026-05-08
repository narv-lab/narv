"""perceptor モジュール — interface: perception_v1

環境からの情報収集と StandardizedEvent への抽象化。
- ステートレス設計: 各呼び出しで独立したスナップショットを提供
- gather_perceptions: CLOCK/FILE/USER_INPUT/SHELL_OUTPUT/IDLE_ENTROPY を対応
- abstract_perception: 生データを StandardizedEvent 配列へ変換
- 異常系キーワード (ERROR/FATAL/Exception) 検出時に urgency=0.9 をセット
- IDLE_ENTROPY: アイドル時に合成エントロピーイベントを生成し Σ_env を非静的に保つ (Solution A)
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.core.exceptions import PerceptorError
from src.core.types import StandardizedEvent
from src.core.config import config
from src.core.logger import setup_logger

logger = setup_logger("perceptor")

# -------------------------------------------------------------------------
# 定数
# -------------------------------------------------------------------------
ANOMALY_PATTERN = re.compile(
    r"\b(ERROR|FATAL|Exception|CRITICAL|PANIC|Traceback)\b", re.IGNORECASE
)


# -------------------------------------------------------------------------
# Perceptor クラス
# -------------------------------------------------------------------------
class Perceptor:
    """環境イベント収集クラス。ステートレス設計。perception_v1 インタフェースを実装する。"""

    def __init__(self, watch_dirs: Optional[list[str]] = None, sandbox_root: Optional[str] = None) -> None:
        """
        Args:
            watch_dirs: FILE ソース監視対象のディレクトリ一覧
            sandbox_root: ファイル変更監視のルートパス
        """
        self._watch_dirs: list[Path] = [Path(d) for d in (watch_dirs or [])]
        logger.info("Perceptor initialized. watch_dirs=%s", self._watch_dirs)

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------
    def _make_event(self, source: str, urgency: float, payload: Any) -> dict:
        """StandardizedEvent の辞書表現を生成する。"""
        event = StandardizedEvent(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            source=source,
            urgency=urgency,
            payload=payload,
        )
        return event.model_dump(mode="json")

    def _scan_clock(self) -> dict:
        """時計イベント: 現在時刻スナップショットを生成する。"""
        now = datetime.now(timezone.utc)
        return self._make_event(
            source="CLOCK",
            urgency=0.0,
            payload={"iso8601": now.isoformat(), "epoch": now.timestamp()},
        )

    def _scan_files(self, since_epoch: Optional[float] = None) -> list[dict]:
        """ファイルスキャン: スナップショットとして現在のファイル群の状態と異常系スキャン結果を返す。"""
        events = []
        for watch_dir in self._watch_dirs:
            if not watch_dir.exists():
                continue
            for path in watch_dir.rglob("*"):
                if not path.is_file() or path.suffix in [".log", ".out"]:  # SHELL_OUTPUTと分ける
                    continue
                try:
                    mtime = path.stat().st_mtime
                    if since_epoch is not None and mtime < since_epoch:
                        continue
                    text = path.read_text(errors="ignore")[:10000]
                    has_anomaly = bool(ANOMALY_PATTERN.search(text))
                    urgency = config.perceptor_urgency_anomaly if has_anomaly else config.perceptor_urgency_normal
                    events.append(self._make_event(
                        source="FILE",
                        urgency=urgency,
                        payload={"path": str(path), "mtime": mtime, "has_anomaly": has_anomaly},
                    ))
                except OSError:
                    pass
        return events

    def _scan_user_input(self, since_epoch: Optional[float] = None) -> list[dict]:
        """標準入力と指定ファイルからの非ブロッキングスキャン。"""
        import os
        import select
        from pathlib import Path
        events = []
        
        # stdin がターミナルに接続されていない場合のみパイプ入力をチェック
        if not sys.stdin.isatty():
             try:
                 ready, _, _ = select.select([sys.stdin], [], [], 0)
                 if ready:
                     line = sys.stdin.readline().rstrip("\n")
                     if line:
                         urgency = config.perceptor_urgency_anomaly if ANOMALY_PATTERN.search(line) else config.perceptor_urgency_normal
                         events.append(self._make_event(
                             source="USER_INPUT",
                             urgency=urgency,
                             payload={"text": line},
                         ))
             except Exception:
                 pass

        # サンドボックス内の user_input.txt からも読み取る
        sandbox_root = Path(os.getenv("ACTOR_SANDBOX_ROOT", config.actor_sandbox_root)).resolve()
        input_file = (sandbox_root / "user_input.txt").resolve()
        
        if input_file.exists():
            try:
                mtime = input_file.stat().st_mtime
                # 0.5秒の猶予を持たせる（クロック微差対策）
                if since_epoch is not None and mtime < (since_epoch - 0.5):
                    logger.debug("Skipping old user input file: mtime=%.1f since=%.1f", mtime, since_epoch)
                    return events
                
                logger.info("New user input file detected: %s", input_file)
                # 複数行ある可能性を考慮し、全行読み取る
                lines = input_file.read_text(encoding="utf-8").splitlines()
                
                # イベント作成処理
                processed_events = []
                for line in lines:
                    line = line.strip()
                    if line:
                        urgency = config.perceptor_urgency_anomaly if ANOMALY_PATTERN.search(line) else config.perceptor_urgency_normal
                        processed_events.append(self._make_event(
                            source="USER_INPUT",
                            urgency=urgency,
                            payload={"text": line},
                        ))
                
                # すべてのイベントが正常に作成できた場合のみ、ファイルを削除する
                events.extend(processed_events)
                input_file.unlink()
                logger.info("Processed and removed user input file: %d events", len(processed_events))
            except OSError as exc:
                logger.error("Failed to process user_input.txt: %s", exc)
        else:
            # logger.debug("User input file not found at: %s", input_file)
            pass

        return events

    def _scan_shell_output(self, since_epoch: Optional[float] = None) -> list[dict]:
        """SHELL_OUTPUTスキャン: 指定層ディレクトリ内のログファイル（*.log, *.out）をスキャンする。"""
        events = []
        for watch_dir in self._watch_dirs:
            if not watch_dir.exists():
                continue
            for ext in ("*.log", "*.out"):
                for path in watch_dir.rglob(ext):
                    if not path.is_file():
                        continue
                    try:
                        mtime = path.stat().st_mtime
                        if since_epoch is not None and mtime < since_epoch:
                            continue
                        text = path.read_text(errors="ignore")[-10000:]  # 末尾1万文字
                        has_anomaly = bool(ANOMALY_PATTERN.search(text))
                        urgency = config.perceptor_urgency_anomaly if has_anomaly else config.perceptor_urgency_shell_output
                        events.append(self._make_event(
                            source="SHELL_OUTPUT",
                            urgency=urgency,
                            payload={"path": str(path), "mtime": mtime, "has_anomaly": has_anomaly},
                        ))
                    except OSError:
                        pass
        return events

    # ------------------------------------------------------------------
    # 内部: 合成エントロピーイベント生成 (Solution A)
    # ------------------------------------------------------------------
    def _generate_idle_entropy(
        self,
        idle_context: Optional[dict] = None,
    ) -> list[dict]:
        """IDLE_ENTROPY: アイドルサイクル時に内部状態由来の合成エントロピーイベントを生成する。

        合成エントロピーは外部環境の変化がない場合でも Σ_env を非静的に保ち、
        Transformer の入力コンテキストが同一アトラクタに収束することを防ぐ (Solution A)。

        生成される情報:
          - アイドル継続サイクル数
          - 現在時刻ナノ秒精度のエポック（微細な差分を保証）
          - goal_staleness_sec: ゴール未変更の経過秒数
          - session_event_count: session_memory の件数（記憶の鮮度を示す）
          - emotion_mu_snapshot: 現在の感情状態スナップショット

        Args:
            idle_context: kernel から渡される内部状態の辞書
              {
                idle_cycle_count: int,
                goal_last_updated_epoch: float,  # optional
                session_event_count: int,         # optional
                emotion_mu: float,               # optional
              }
        """
        import time as _time
        ctx = idle_context or {}
        now = datetime.now(timezone.utc)
        epoch_ns = _time.time_ns()  # ナノ秒精度 — 毎回必ず異なる値

        idle_cycle_count = int(ctx.get("idle_cycle_count", 0))
        goal_last_updated = float(ctx.get("goal_last_updated_epoch", now.timestamp()))
        goal_staleness_sec = now.timestamp() - goal_last_updated
        session_event_count = int(ctx.get("session_event_count", 0))
        emotion_mu = float(ctx.get("emotion_mu", 0.0))

        payload = {
            "source_type": "IDLE_ENTROPY",
            "epoch_ns": epoch_ns,
            "idle_cycle_count": idle_cycle_count,
            "goal_staleness_sec": round(goal_staleness_sec, 3),
            "session_event_count": session_event_count,
            "emotion_mu_snapshot": round(emotion_mu, 4),
            "description": (
                f"IDLE継続{idle_cycle_count}サイクル / "
                f"ゴール未変更{round(goal_staleness_sec)}秒 / "
                f"記憶{session_event_count}件 / "
                f"μ={emotion_mu:.3f}"
            ),
        }
        event = self._make_event(
            source="IDLE_ENTROPY",
            urgency=0.05,  # 非常に低い urgency — IDLE ラウティングに影響させない
            payload=payload,
        )
        logger.debug(
            "IDLE_ENTROPY generated. idle_cycle=%d goal_staleness=%.1fs",
            idle_cycle_count, goal_staleness_sec,
        )
        return [event]

    # ------------------------------------------------------------------
    # 公開 API (perception_v1)
    # ------------------------------------------------------------------
    def gather_perceptions(
        self,
        sources: list[str],
        caller_id: Optional[str] = None,
        since_timestamp: Optional[str] = None,
        idle_context: Optional[dict] = None,
    ) -> dict:
        """指定されたソースから環境イベントを収集する (gather_perceptions 操作).

        Args:
            sources: 'CLOCK' | 'FILE' | 'USER_INPUT' | 'SHELL_OUTPUT' | 'IDLE_ENTROPY' の配列
            caller_id: カーネルが注入する呼び出し元識別子
            since_timestamp: この時刻以降のイベントのみを返す (FILE/SHELL_OUTPUT)
            idle_context: IDLE_ENTROPY ソース使用時に渡す内部状態辞書 (Solution A)

        Returns:
            { perceptions: list[StandardizedEvent dict], timestamp: str }
        Raises:
            PerceptorError: SENSOR_TIMEOUT
            RuntimeError: SECURITY_VIOLATION
        """
        if caller_id != "kernel":
            raise RuntimeError("SECURITY_VIOLATION: caller_id must be 'kernel'")

        timestamp = datetime.now(timezone.utc).isoformat()
        perceptions: list[dict] = []
        
        since_epoch: Optional[float] = None
        if since_timestamp:
            try:
                dt_str = since_timestamp.replace("Z", "+00:00")
                since_epoch = datetime.fromisoformat(dt_str).timestamp()
            except ValueError:
                pass

        try:
            for source in sources:
                if source == "CLOCK":
                    perceptions.append(self._scan_clock())
                elif source == "FILE":
                    perceptions.extend(self._scan_files(since_epoch))
                elif source == "USER_INPUT":
                    perceptions.extend(self._scan_user_input(since_epoch))
                elif source == "SHELL_OUTPUT":
                    perceptions.extend(self._scan_shell_output(since_epoch))
                elif source == "IDLE_ENTROPY":
                    perceptions.extend(self._generate_idle_entropy(idle_context))
                else:
                    logger.warning("Unknown perception source: %s", source)

            logger.debug(
                "gather_perceptions caller_id=%s sources=%s count=%d",
                caller_id, sources, len(perceptions),
            )
            return {"perceptions": perceptions, "timestamp": timestamp}
        except PerceptorError:
            raise
        except Exception as exc:
            # FINDING-PERCEPTOR-002: プログラミングエラーを SENSOR_TIMEOUT と区別してログ出力
            logger.exception("Unexpected error in gather_perceptions: %s", exc)
            raise PerceptorError(PerceptorError.SENSOR_TIMEOUT, str(exc)) from exc

    def abstract_perception(
        self,
        raw_data: dict,
        caller_id: Optional[str] = None,
    ) -> dict:
        """生データを抽象概念の配列へ変換する (abstract_perception 操作).

        異常系キーワードが含まれている場合は urgency を高く設定して
        StandardizedEvent として正規化する。

        Args:
            raw_data: 任意の辞書形式の生データ
            caller_id: カーネルが注入する呼び出し元識別子

        Returns:
            { abstract_concepts: list[dict] }
        Raises:
            PerceptorError: ABSTRACTION_ERROR
            RuntimeError: SECURITY_VIOLATION
        """
        if caller_id != "kernel":
            raise RuntimeError("SECURITY_VIOLATION: caller_id must be 'kernel'")

        try:
            text = str(raw_data.get("text", raw_data.get("payload", str(raw_data))))
            has_anomaly = bool(ANOMALY_PATTERN.search(text))
            urgency = config.perceptor_urgency_anomaly if has_anomaly else config.perceptor_urgency_normal

            abstract_event = self._make_event(
                source=raw_data.get("source", "UNKNOWN"),
                urgency=urgency,
                payload={"raw": raw_data, "has_anomaly": has_anomaly},
            )
            logger.debug("abstract_perception caller_id=%s urgency=%.1f", caller_id, urgency)
            return {"abstract_concepts": [abstract_event]}
        except Exception as exc:
            raise PerceptorError(PerceptorError.ABSTRACTION_ERROR, str(exc)) from exc
