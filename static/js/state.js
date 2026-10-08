/* state.js — the single client-side copy of the server configuration. */

import { Emitter } from "./lib.js";

export const SECTIONS = ["aircraft", "environment", "launch", "simulation"];

export class ConfigStore extends Emitter {
  constructor() {
    super();
    this.data = { aircraft: {}, environment: {}, launch: {}, simulation: {} };
    this.schema = { aircraft: {}, environment: {}, launch: {} };
    this.air = {};
    this.mesh = null;
    this.meshModels = [];
  }

  /** Replace the local copy with a server snapshot (boot / import). */
  adopt(snapshot) {
    for (const section of SECTIONS) {
      if (snapshot && snapshot[section] && typeof snapshot[section] === "object") {
        this.data[section] = { ...snapshot[section] };
      }
    }
    if (snapshot && typeof snapshot.selected_mesh_model_id === "string") {
      this.meshModelId = snapshot.selected_mesh_model_id;
    }
    this.emit("adopt", this.data);
    this.emit("change", { section: null, bulk: true });
  }

  setSchema(schema) {
    this.schema = {
      aircraft: (schema && schema.aircraft && schema.aircraft.fields) || {},
      environment: (schema && schema.environment) || {},
      launch: (schema && schema.launch) || {},
      raw: schema || {},
    };
    this.emit("schema", this.schema);
  }

  get(section, key) {
    const bucket = this.data[section];
    return bucket ? bucket[key] : undefined;
  }

  /** Write one field. `origin` is echoed back so panels can ignore echoes. */
  set(section, key, value, origin = "ui") {
    if (!this.data[section]) this.data[section] = {};
    if (this.data[section][key] === value) return false;
    this.data[section][key] = value;
    this.emit("change", { section, key, value, origin });
    return true;
  }

  /** Write several fields of one section at once (single change event). */
  merge(section, patch, origin = "ui") {
    if (!this.data[section]) this.data[section] = {};
    let changed = false;
    for (const [key, value] of Object.entries(patch)) {
      if (this.data[section][key] !== value) {
        this.data[section][key] = value;
        changed = true;
      }
    }
    if (changed) this.emit("change", { section, bulk: true, origin });
    return changed;
  }

  /** Restore a section to the server defaults declared by the schema. */
  resetSection(section) {
    const fields = this.schema[section] && this.schema[section].fields;
    if (!fields) return false;
    const patch = {};
    for (const [key, spec] of Object.entries(fields)) {
      if (spec && Object.prototype.hasOwnProperty.call(spec, "default")) {
        patch[key] = spec.default;
      }
    }
    return this.merge(section, patch, "reset");
  }

  setAir(payload) {
    this.air = { ...(this.air || {}), ...(payload || {}) };
    if (payload && payload.environment) {
      // The atmosphere response echoes the clamped environment back.
      this.data.environment = { ...payload.environment };
    }
    this.emit("air", this.air);
  }
}

export const store = new ConfigStore();