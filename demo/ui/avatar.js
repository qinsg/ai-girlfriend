// @ts-check

import { $ } from "./dom.js";

const VISEME_COUNT = 6;
const VISEME_INTERVAL_MS = 55;
const VISEME_GATE = 0.025;
const VISEME_FULL_SCALE = 0.46;

/**
 * Keeps the LivePortrait idle stream visible and overlays one cached, sharp
 * mouth pose while assistant audio is playing. No per-response video render is
 * involved: the already-playing output analyser selects a pose in real time.
 */
export class AvatarView {
  /** @param {{onState?: (state: "playing" | "idle") => void}} [options] */
  constructor(options = {}) {
    /** @type {HTMLElement} */
    this.card = $("#avatar-card");
    /** @type {HTMLImageElement} */
    this.image = $("#avatar-image");
    /** @type {HTMLVideoElement} */
    this.idleVideo = $("#avatar-idle-video");
    /** @type {HTMLImageElement} */
    this.visemeImage = $("#avatar-viseme-image");
    /** @type {HTMLElement} */
    this.name = $("#avatar-name");
    /** @type {HTMLElement} */
    this.status = $("#avatar-status");
    this.enabled = false;
    this.speaking = false;
    this.audioActive = false;
    this.visemesReady = false;
    this.character = "xiaoman";
    this.visemeLevel = 0;
    this.lastVisemeUpdate = 0;
    this.lastAudibleAt = 0;
    this.loadSequence = 0;
    /** @type {string[]} */
    this.visemeUrls = [];
    this.onState = options.onState || (() => {});

    this.idleVideo.addEventListener("loadeddata", () => {
      this.idleVideo.classList.add("ready");
      if (!this.speaking) this.status.textContent = "本地数字人在线";
    });
    this.idleVideo.addEventListener("error", () => {
      this.idleVideo.classList.remove("ready");
      if (this.enabled) this.status.textContent = "待机视频加载失败";
    });

    const tick = (now) => {
      this._updateViseme(now);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  /** @param {boolean} enabled */
  setEnabled(enabled) {
    this.enabled = enabled;
    this.card.classList.toggle("avatar-enabled", enabled);
    if (enabled) {
      this._loadIdleVideo();
      void this._loadVisemes();
    } else {
      this._unloadIdleVideo();
      this._hideViseme();
    }
    this.status.textContent = enabled ? "正在加载待机画面" : "静态角色照片";
  }

  /** @param {string} character @param {{label?: string, avatar?: string}} [preset] */
  setCharacter(character, preset = {}) {
    this.cancel();
    this.character = character || "xiaoman";
    this.image.src = preset.avatar || `assets/avatars/${this.character}.png`;
    this.image.alt = (preset.label || this.character).split("·")[0].trim();
    this.name.textContent = this.image.alt;
    if (this.enabled) {
      this._loadIdleVideo();
      void this._loadVisemes();
    }
  }

  /** Kept for the shared settings contract; viseme images produce no audio. */
  setAudioOutput(_deviceId) {}

  /** @param {boolean} speaking */
  setSpeaking(speaking) {
    const next = Boolean(speaking && this.enabled);
    if (next === this.speaking) return;
    this.speaking = next;
    if (next) {
      this.status.textContent = this.visemesReady ? "正在说话" : "正在准备高清口型";
    } else {
      // response.done can arrive while the AudioWorklet still has queued PCM.
      // The analyser below owns the visual stop time, so do not hide early.
      if (!this.audioActive) {
        this._hideViseme();
        this.status.textContent = this.enabled ? "本地数字人在线" : "静态角色照片";
      }
    }
  }

  cancel() {
    this.setSpeaking(false);
  }

  _loadIdleVideo() {
    const url = `api/avatar/idle/${encodeURIComponent(this.character)}?v=idle-stream-v2`;
    if (this.idleVideo.getAttribute("src") === url) return;
    this.idleVideo.classList.remove("ready");
    this.idleVideo.src = url;
    this.idleVideo.load();
    void this.idleVideo.play().catch(() => {});
  }

  async _loadVisemes() {
    const sequence = ++this.loadSequence;
    this.visemesReady = false;
    this._hideViseme();
    const character = encodeURIComponent(this.character);
    const urls = Array.from(
      { length: VISEME_COUNT },
      (_, level) => `api/avatar/viseme/${character}/${level}?v=viseme-v4`,
    );
    try {
      await Promise.all(urls.map((url) => new Promise((resolve, reject) => {
        const preload = new Image();
        preload.onload = resolve;
        preload.onerror = reject;
        preload.src = url;
      })));
      if (sequence !== this.loadSequence) return;
      this.visemeUrls = urls;
      this.visemeImage.src = urls[0];
      this.visemesReady = true;
      this.status.textContent = this.speaking ? "正在说话" : "本地数字人在线";
    } catch (error) {
      if (sequence !== this.loadSequence) return;
      console.warn("[avatar] high-resolution visemes failed to load:", error);
      this.status.textContent = "高清口型加载失败";
    }
  }

  /** @param {number} now */
  _updateViseme(now) {
    if (!this.enabled || !this.visemesReady) {
      this._hideViseme();
      return;
    }
    if (now - this.lastVisemeUpdate < VISEME_INTERVAL_MS) return;
    this.lastVisemeUpdate = now;
    const raw = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--ai-audio-level"),
    ) || 0;
    if (raw > VISEME_GATE) {
      this.lastAudibleAt = now;
      if (!this.audioActive) {
        this.audioActive = true;
        this.status.textContent = "正在说话";
        this.onState("playing");
      }
    } else if (!this.audioActive || now - this.lastAudibleAt > 140) {
      if (this.audioActive) {
        this.audioActive = false;
        this.status.textContent = this.enabled ? "本地数字人在线" : "静态角色照片";
        this.onState("idle");
      }
      this._hideViseme();
      return;
    }
    const normalized = Math.max(0, Math.min(1, (raw - VISEME_GATE) / VISEME_FULL_SCALE));
    let next = Math.round(Math.pow(normalized, 0.72) * (VISEME_COUNT - 1));
    // Let the mouth open quickly, but close one pose at a time to avoid chatter.
    if (next < this.visemeLevel) next = Math.max(next, this.visemeLevel - 1);
    if (next !== this.visemeLevel) {
      this.visemeLevel = next;
      this.visemeImage.src = this.visemeUrls[next];
    }
    this.visemeImage.classList.add("active");
  }

  _hideViseme() {
    this.visemeLevel = 0;
    this.visemeImage.classList.remove("active");
    if (this.visemeUrls[0]) this.visemeImage.src = this.visemeUrls[0];
  }

  _unloadIdleVideo() {
    this.loadSequence += 1;
    this.visemesReady = false;
    this.audioActive = false;
    this.idleVideo.pause();
    this.idleVideo.classList.remove("ready");
    this.idleVideo.removeAttribute("src");
    this.idleVideo.load();
  }
}
