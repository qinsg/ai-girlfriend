// @ts-check

import { $ } from "./dom.js";

const AVATAR_FPS = 25;
const PCM_BYTES_PER_MS = 48;

/**
 * Keeps the character alive continuously and queues short lip-sync clips over
 * the idle stream. Rendering and playback run independently, so the next clip
 * can be prepared while the current one is playing.
 */
export class AvatarView {
  /** @param {{onState?: (state: "preparing" | "playing" | "idle") => void}} [options] */
  constructor(options = {}) {
    /** @type {HTMLElement} */
    this.card = $("#avatar-card");
    /** @type {HTMLImageElement} */
    this.image = $("#avatar-image");
    /** @type {HTMLVideoElement} */
    this.idleVideo = $("#avatar-idle-video");
    /** @type {HTMLVideoElement} */
    this.speechVideo = $("#avatar-speech-video");
    /** @type {HTMLElement} */
    this.name = $("#avatar-name");
    /** @type {HTMLElement} */
    this.status = $("#avatar-status");
    this.enabled = false;
    this.character = "xiaoman";
    this.audioOutputId = "";
    this.sequence = 0;
    this.responseId = "";
    this.nextFrame = 0;
    this.pendingRenders = 0;
    this.streamEnded = true;
    this.playing = false;
    this.renderTail = Promise.resolve();
    /** @type {{url?: string, pcm: ArrayBuffer, fallback: (pcm: ArrayBuffer) => void}[]} */
    this.playbackQueue = [];
    /** @type {Set<AbortController>} */
    this.requests = new Set();
    this.onState = options.onState || (() => {});

    this.idleVideo.addEventListener("loadeddata", () => {
      this.idleVideo.classList.add("ready");
      if (!this.playing && this.pendingRenders === 0) this.status.textContent = "本地数字人在线";
    });
    this.idleVideo.addEventListener("error", () => {
      this.idleVideo.classList.remove("ready");
      if (this.enabled) this.status.textContent = "待机视频加载失败";
    });
  }

  /** @param {boolean} enabled */
  setEnabled(enabled) {
    this.enabled = enabled;
    this.card.classList.toggle("avatar-enabled", enabled);
    if (enabled) this._loadIdleVideo();
    else this._unloadIdleVideo();
    this.status.textContent = enabled ? "正在加载待机画面" : "静态角色照片";
  }

  /** @param {string} character @param {{label?: string, avatar?: string}} [preset] */
  setCharacter(character, preset = {}) {
    this.cancel();
    this.character = character || "xiaoman";
    this.image.src = preset.avatar || `assets/avatars/${this.character}.png`;
    this.image.alt = (preset.label || this.character).split("·")[0].trim();
    this.name.textContent = this.image.alt;
    if (this.enabled) this._loadIdleVideo();
  }

  /** @param {string} deviceId */
  setAudioOutput(deviceId) {
    this.audioOutputId = deviceId || "";
  }

  /**
   * Queue one short TTS audio chunk. The render requests stay ordered because
   * each chunk continues from the frame where the preceding one ended.
   * @param {{audio: Blob, pcm: ArrayBuffer, responseId: string, chunkIndex?: number}} detail
   * @param {(pcm: ArrayBuffer) => void} fallback
   */
  renderChunk(detail, fallback) {
    if (!this.enabled) {
      fallback(detail.pcm);
      return;
    }

    if (detail.responseId !== this.responseId) {
      this.cancel();
      this.responseId = detail.responseId;
      this.streamEnded = false;
      this.nextFrame = Math.max(0, Math.floor((this.idleVideo.currentTime || 0) * AVATAR_FPS));
    }

    const sequence = this.sequence;
    this.pendingRenders += 1;
    this.card.classList.add("preparing-speech");
    this.status.textContent = this.playing ? "正在说话" : "正在准备第一段口型";
    if (!this.playing) this.onState("preparing");

    this.renderTail = this.renderTail
      .catch(() => {})
      .then(() => this._renderOne(detail, fallback, sequence))
      .finally(() => {
        if (sequence !== this.sequence) return;
        this.pendingRenders = Math.max(0, this.pendingRenders - 1);
        if (this.pendingRenders === 0) this.card.classList.remove("preparing-speech");
        this._settleIfIdle();
      });
  }

  /** @param {string} responseId */
  finishResponse(responseId) {
    if (responseId !== this.responseId) return;
    this.streamEnded = true;
    this._settleIfIdle();
  }

  /** @param {{audio: Blob, pcm: ArrayBuffer}} detail @param {(pcm: ArrayBuffer) => void} fallback @param {number} sequence */
  async _renderOne(detail, fallback, sequence) {
    const request = new AbortController();
    this.requests.add(request);
    try {
      const response = await fetch(
        `api/avatar/lipsync?character=${encodeURIComponent(this.character)}&start_frame=${this.nextFrame}`,
        {
          method: "POST",
          headers: { "Content-Type": "audio/wav" },
          body: detail.audio,
          signal: request.signal,
        },
      );
      if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
      const videoBlob = await response.blob();
      if (sequence !== this.sequence) return;
      this.nextFrame = Number(response.headers.get("x-avatar-next-frame")) || this.nextFrame;
      this.playbackQueue.push({ url: URL.createObjectURL(videoBlob), pcm: detail.pcm, fallback });
    } catch (error) {
      if (request.signal.aborted || sequence !== this.sequence) return;
      console.warn("[avatar] chunk render failed, falling back to audio:", error);
      this.playbackQueue.push({ pcm: detail.pcm, fallback });
    } finally {
      this.requests.delete(request);
    }
    void this._pumpPlayback(sequence);
  }

  /** @param {number} sequence */
  async _pumpPlayback(sequence) {
    if (this.playing || sequence !== this.sequence) return;
    this.playing = true;
    try {
      while (this.playbackQueue.length && sequence === this.sequence) {
        const clip = this.playbackQueue.shift();
        if (!clip) continue;
        if (clip.url) {
          await this._playSpeechClip(clip.url, sequence);
          URL.revokeObjectURL(clip.url);
        } else {
          clip.fallback(clip.pcm);
          await new Promise((resolve) => setTimeout(resolve, clip.pcm.byteLength / PCM_BYTES_PER_MS));
        }
      }
    } finally {
      if (sequence === this.sequence) {
        this.playing = false;
        this.speechVideo.classList.remove("playing");
        this._settleIfIdle();
      }
    }
  }

  /** @param {string} url @param {number} sequence */
  async _playSpeechClip(url, sequence) {
    this.speechVideo.src = url;
    this.speechVideo.currentTime = 0;
    if (this.audioOutputId && typeof this.speechVideo.setSinkId === "function") {
      await this.speechVideo.setSinkId(this.audioOutputId);
    }
    if (sequence !== this.sequence) return;
    this.speechVideo.classList.add("playing");
    this.status.textContent = "正在说话";
    this.onState("playing");
    await this.speechVideo.play();
    await new Promise((resolve) => {
      const done = () => {
        this.speechVideo.removeEventListener("ended", done);
        this.speechVideo.removeEventListener("error", done);
        resolve(undefined);
      };
      this.speechVideo.addEventListener("ended", done, { once: true });
      this.speechVideo.addEventListener("error", done, { once: true });
    });
  }

  cancel() {
    this.sequence += 1;
    for (const request of this.requests) request.abort();
    this.requests.clear();
    for (const clip of this.playbackQueue) if (clip.url) URL.revokeObjectURL(clip.url);
    this.playbackQueue = [];
    this.renderTail = Promise.resolve();
    this.pendingRenders = 0;
    this.streamEnded = true;
    this.responseId = "";
    this.playing = false;
    this.card.classList.remove("preparing-speech");
    this.speechVideo.pause();
    this.speechVideo.classList.remove("playing");
    this.speechVideo.removeAttribute("src");
    this.speechVideo.load();
    this.status.textContent = this.enabled ? "本地数字人在线" : "静态角色照片";
    this.onState("idle");
  }

  _loadIdleVideo() {
    const url = `api/avatar/idle/${encodeURIComponent(this.character)}?v=idle-stream-v1`;
    if (this.idleVideo.getAttribute("src") === url) return;
    this.idleVideo.classList.remove("ready");
    this.idleVideo.src = url;
    this.idleVideo.load();
    void this.idleVideo.play().catch(() => {
      // The video is muted, so modern browsers normally allow autoplay. A
      // user gesture from starting the call gives us another chance later.
    });
  }

  _unloadIdleVideo() {
    this.idleVideo.pause();
    this.idleVideo.classList.remove("ready");
    this.idleVideo.removeAttribute("src");
    this.idleVideo.load();
  }

  _settleIfIdle() {
    if (this.pendingRenders || this.playing || this.playbackQueue.length || !this.streamEnded) return;
    this.card.classList.remove("preparing-speech");
    this.status.textContent = "本地数字人在线";
    this.onState("idle");
  }
}
