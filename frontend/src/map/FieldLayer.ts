import { BitmapLayer } from "@deck.gl/layers";
import type { FieldClasses } from "../lib/field";
import { FIELD_ALPHA, FIELD_RAMP, hex } from "../lib/palette";

const f = (n: number) => (Number.isInteger(n) ? `${n}.0` : `${n}`);

function rgb(h: string): string {
  const [r, g, b] = hex(h);
  return `${f(r / 255)}, ${f(g / 255)}, ${f(b / 255)}`;
}

/**
 * GLSL for DECKGL_FILTER_COLOR: the texture carries a quantized value in R (validity in A), the
 * GPU interpolates it bilinearly, and this classes it into anti-aliased bands with isolines at
 * the decision thresholds. Crisp at every zoom with no per-pixel CPU work.
 */
export function fieldShader(classes: FieldClasses, step: number, offset: number): string {
  const bands = classes.bins
    .map((b, i) => `  c = mix(c, vec4(${rgb(FIELD_RAMP[i])}, ${f(FIELD_ALPHA[i])}), smoothstep(${f(b)} - w, ${f(b)} + w, v));`)
    .join("\n");
  const isos = classes.isolines
    .map((t) => `  iso = max(iso, 1.0 - smoothstep(0.0, w * 1.6, abs(v - ${f(t)})));`)
    .join("\n");
  return `
  float v = color.r * 255.0 * ${f(step)} + ${f(offset)};
  float valid = color.a;
  float w = max(fwidth(v) * 0.9, 0.02);
  vec4 c = vec4(${rgb(FIELD_RAMP[0])}, 0.0); // start transparent in the first band's hue: no dark fringe
${bands}
  float iso = 0.0;
${isos}
  c = mix(c, vec4(1.0, 1.0, 1.0, max(c.a, 0.85)), iso * 0.6);
  color = vec4(c.rgb, c.a * valid);
`;
}

type FieldLayerProps = { filterShader: string };

/** BitmapLayer whose texture is data, not colour: see fieldShader. */
export class FieldLayer extends BitmapLayer<FieldLayerProps> {
  static layerName = "FieldLayer";
  static defaultProps = { ...BitmapLayer.defaultProps, filterShader: { type: "string", value: "" } };

  getShaders() {
    const shaders = super.getShaders();
    return {
      ...shaders,
      inject: { ...(shaders.inject ?? {}), "fs:DECKGL_FILTER_COLOR": this.props.filterShader },
    };
  }
}
