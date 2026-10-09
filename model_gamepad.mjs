// Xbox layout follows the browser's standard Gamepad mapping.
export function stickInput(x, y, deadZone = .15) {
  x = Number.isFinite(x) ? Math.max(-1, Math.min(1, x)) : 0;
  y = Number.isFinite(y) ? Math.max(-1, Math.min(1, y)) : 0;
  const length = Math.hypot(x, y);
  if (length <= deadZone) return { x: 0, y: 0 };
  const strength = (Math.min(length, 1) - deadZone) / (1 - deadZone);
  return { x: x / length * strength, y: y / length * strength };
}

function buttonValue(button) {
  const value = Number.isFinite(button?.value) ? button.value : button?.pressed ? 1 : 0;
  return Math.max(0, Math.min(1, value));
}

export class GamepadInput {
  constructor() { this.reset(); }

  reset() {
    this.identity = null;
    this.armed = false;
    this.previousButtons = [];
  }

  read(gamepads, active) {
    const connected = Array.from(gamepads || []).filter(pad => pad?.connected);
    const supported = connected.filter(pad => pad.mapping === 'standard' && pad.axes?.length >= 4 && pad.buttons?.length >= 16);
    const identity = pad => `${pad.index}:${pad.id}`;
    const pad = supported.find(pad => identity(pad) === this.identity) || supported[0];
    if (!pad) {
      this.reset();
      return { state: connected.length ? 'unsupported' : 'waiting' };
    }
    if (identity(pad) !== this.identity) {
      this.reset();
      this.identity = identity(pad);
    }
    const left = stickInput(pad.axes[0], pad.axes[1]);
    const right = stickInput(pad.axes[2], pad.axes[3]);
    const buttons = pad.buttons.map(button => button?.pressed || buttonValue(button) > .5);
    const trigger = index => {
      const value = buttonValue(pad.buttons[index]);
      return value <= .05 ? 0 : (value - .05) / .95;
    };
    const down = trigger(6), up = trigger(7);
    const neutral = !left.x && !left.y && !right.x && !right.y && !up && !down && !buttons.some(Boolean);
    if (!active) {
      this.armed = false;
      this.previousButtons = buttons;
      return { state: 'paused', pad };
    }
    if (!this.armed) {
      this.previousButtons = buttons;
      this.armed = neutral;
      return { state: neutral ? 'active' : 'release', pad };
    }
    const pressed = new Set(buttons.flatMap((value, index) => value && !this.previousButtons[index] ? [index] : []));
    this.previousButtons = buttons;
    return { state: 'active', pad, pressed, look: right,
      movement: { forward: -left.y, right: left.x, up: up - down, sprint: buttons[10] } };
  }
}
