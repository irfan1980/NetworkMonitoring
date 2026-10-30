// Set while a canvas pan is active (cleared on the next touch down), so a node's press handler can ignore the release of a drag.
let moved = false;

export function setPanMoved(value: boolean): void {
  moved = value;
}

export function panMoved(): boolean {
  return moved;
}
