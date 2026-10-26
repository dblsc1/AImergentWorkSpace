import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// 状态文件由 HoneyComb 检测程序写（modules/ai-detector 契约「红绿灯」）：
// {updated_at: Unix 秒, agents: [{key, label, state, detail}]}
const DIR = `${GLib.get_home_dir()}/.agent-traffic-light`;
const STATUS_PATH = `${DIR}/agents.json`;
const CONFIG_PATH = `${DIR}/shell-config.json`;
const STALE_SEC = 10;

const STATE_TEXT = {
    working: 'working',
    waiting_input: 'waiting input',
    waiting_permission: 'permission',
    complete: 'complete',
    idle: 'idle',
    error: 'error',
    unknown: 'unknown',
};

// 灯语：working 绿慢闪；waiting_* 黄快闪；complete/idle 绿常亮；其余（error/unknown）红常亮。
function litColor(state, tick) {
    switch (state) {
    case 'working':
        return tick % 4 < 2 ? 'green' : null;
    case 'waiting_input':
    case 'waiting_permission':
        return tick % 2 === 0 ? 'yellow' : null;
    case 'complete':
    case 'idle':
        return 'green';
    default:
        return 'red';
    }
}

const CURSOR_NAMES = {
    move: 'MOVE',
    e: 'E_RESIZE',
    w: 'W_RESIZE',
    n: 'N_RESIZE',
    s: 'S_RESIZE',
    ne: 'NE_RESIZE',
    nw: 'NW_RESIZE',
    se: 'SE_RESIZE',
    sw: 'SW_RESIZE',
};

function raiseActor(actor) {
    const parent = actor.get_parent();
    if (parent)
        parent.set_child_above_sibling(actor, null);
}

function setCursor(actor, cursorName = null) {
    try {
        if (!actor.set_cursor_type)
            return;
        const cursorType = cursorName ? Clutter.CursorType?.[cursorName] : Clutter.CursorType?.DEFAULT;
        actor.set_cursor_type(cursorType ?? 0);
    } catch (_e) {
    }
}

function cursorForEdge(edge) {
    return edge ? CURSOR_NAMES[edge] : CURSOR_NAMES.move;
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function leftButtonDownFromState(state) {
    return (state & Clutter.ModifierType.BUTTON1_MASK) !== 0;
}

class DragController {
    constructor(owner) {
        this.owner = owner;
        this.target = null;
        this.grab = null;

        // Zero-size reactive actor used purely as the grab holder, mirroring
        // GNOME Shell's own drag-and-drop implementation (js/ui/dnd.js). A
        // Clutter grab redirects every input event to this actor regardless of
        // pointer position, so there is no need for a full-screen capture layer
        // or a polling fallback.
        this.actor = new Clutter.Actor({
            reactive: true,
            width: 0,
            height: 0,
        });
        this.actor.connect('event', (_actor, event) => this._onEvent(event));
        owner.layer.add_child(this.actor);
    }

    // Kept for API compatibility with _resizeLayer(); nothing to do.
    resize() {
    }

    destroy() {
        this._finish(false);
        this.actor.destroy();
    }

    begin(target, cursorName) {
        // Tear down any drag already in flight before starting a new one.
        this._finish(false);
        this.target = target;

        try {
            this.grab = global.stage.grab(this.actor);
        } catch (_e) {
            this.grab = null;
        }

        // If the grab could not be established (e.g. another grab is active),
        // abort cleanly so the target never gets stuck mid-drag.
        if (!this.grab) {
            this.target = null;
            target?.cancelDrag?.();
            return;
        }

        setCursor(global.stage, cursorName);
    }

    _onEvent(event) {
        if (!this.target)
            return Clutter.EVENT_PROPAGATE;

        const type = event.type();

        if (type === Clutter.EventType.MOTION || type === Clutter.EventType.TOUCH_UPDATE) {
            // Safety net: if the button is no longer held (e.g. a release was
            // somehow missed), commit and stop.
            if (type === Clutter.EventType.MOTION && event.get_state &&
                !leftButtonDownFromState(event.get_state())) {
                this._finish(true);
                return Clutter.EVENT_STOP;
            }
            const [x, y] = event.get_coords();
            this.target.dragMotion(x, y);
            return Clutter.EVENT_STOP;
        }

        if (type === Clutter.EventType.BUTTON_RELEASE ||
            type === Clutter.EventType.TOUCH_END ||
            type === Clutter.EventType.TOUCH_CANCEL) {
            this._finish(true);
            return Clutter.EVENT_STOP;
        }

        if (type === Clutter.EventType.KEY_PRESS &&
            event.get_key_symbol() === Clutter.KEY_Escape) {
            this._finish(false);
            return Clutter.EVENT_STOP;
        }

        // Swallow everything else for the duration of the drag.
        return Clutter.EVENT_STOP;
    }

    _finish(commit) {
        const target = this.target;
        this.target = null;

        if (this.grab) {
            try {
                this.grab.dismiss();
            } catch (_e) {
            }
            this.grab = null;
        }

        setCursor(global.stage);

        if (!target)
            return;
        if (commit)
            target.endDrag();
        else
            target.cancelDrag?.();
    }
}

function readJson(path, fallback = null) {
    try {
        const [ok, bytes] = Gio.File.new_for_path(path).load_contents(null);
        if (!ok)
            return fallback;
        return JSON.parse(new TextDecoder().decode(bytes));
    } catch (_e) {
        return fallback;
    }
}

function writeJson(path, value) {
    try {
        const file = Gio.File.new_for_path(path);
        const parent = file.get_parent();
        if (parent && !parent.query_exists(null))
            parent.make_directory_with_parents(null);
        file.replace_contents(
            JSON.stringify(value, null, 2), null, false,
            Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    } catch (e) {
        console.error(`agent-traffic-light: cannot write ${path}: ${e.message}`);
    }
}

function parseTimestamp(v) {
    if (typeof v === 'number' && Number.isFinite(v))
        return v > 1e12 ? v / 1000 : v;
    if (typeof v === 'string') {
        const t = Date.parse(v);
        if (Number.isFinite(t))
            return t / 1000;
    }
    return NaN;
}

function onStage(x, y) {
    return [
        clamp(x, 0, Math.max(0, global.stage.width - 40)),
        clamp(y, 0, Math.max(0, global.stage.height - 24)),
    ];
}

function makeButton(text, onClick) {
    const b = new St.Label({text, style_class: 'atl-btn', reactive: true});
    b.connect('button-press-event', () => {
        onClick();
        return Clutter.EVENT_STOP;
    });
    return b;
}

function showButtons(buttons, on) {
    buttons.opacity = on ? 255 : 0;
    buttons.reactive = on;
    for (const b of buttons.get_children())
        b.reactive = on;
}

class Store {
    constructor() {
        this.data = readJson(CONFIG_PATH, {});
        if (!this.data || typeof this.data !== 'object' || Array.isArray(this.data))
            this.data = {};
        this.data.agents ??= {};
        this.data.ui ??= {};
    }

    agent(key) {
        this.data.agents[key] ??= {};
        return this.data.agents[key];
    }

    save() {
        writeJson(CONFIG_PATH, this.data);
    }

    visible(key) {
        return this.data.agents[key]?.visible !== false;
    }

    setVisible(key, visible) {
        this.agent(key).visible = visible;
        this.save();
    }

    showAll() {
        for (const item of Object.values(this.data.agents))
            item.visible = true;
        this.save();
    }

    get minimized() {
        return this.data.ui.minimized === true;
    }

    set minimized(v) {
        this.data.ui.minimized = Boolean(v);
        this.save();
    }

    geometry(key) {
        const item = this.data.agents[key];
        if (item && [item.x, item.y, item.width, item.height].every(Number.isFinite))
            return item;
        return null;
    }

    setGeometry(key, x, y, width, height) {
        Object.assign(this.agent(key), {
            x: Math.round(x),
            y: Math.round(y),
            width: Math.round(width),
            height: Math.round(height),
        });
        this.save();
    }

    miniPos() {
        const m = this.data.ui.mini;
        return m && Number.isFinite(m.x) && Number.isFinite(m.y) ? m : null;
    }

    setMiniPos(x, y) {
        this.data.ui.mini = {x: Math.round(x), y: Math.round(y)};
        this.save();
    }
}

// 完整红绿灯（灯壳 + 三盏灯），无外框；悬停才出现 − ×。
class AgentActor {
    constructor(owner, item, index) {
        this.owner = owner;
        this.item = item;
        this.key = item.key;
        this.tick = 0;
        this.drag = null;
        this.lastClick = null;

        this.actor = new St.BoxLayout({
            vertical: true,
            reactive: true,
            style_class: 'atl-window',
            track_hover: true,
        });

        this.titleRow = new St.BoxLayout({style_class: 'atl-title-row'});
        this.title = new St.Label({style_class: 'atl-title', x_expand: true});
        this.buttons = new St.BoxLayout({style_class: 'atl-btns'});
        showButtons(this.buttons, false);
        this.buttons.add_child(makeButton('−', () => owner.setMinimized(true)));
        this.buttons.add_child(makeButton('×', () => owner.setVisible(this.key, false)));
        this.titleRow.add_child(this.title);
        this.titleRow.add_child(this.buttons);
        this.actor.add_child(this.titleRow);

        this.body = new St.BoxLayout({style_class: 'atl-body'});
        this.actor.add_child(this.body);

        this.frame = new St.BoxLayout({style_class: 'atl-frame'});
        this.body.add_child(this.frame);
        this.lights = {};
        for (const color of ['red', 'yellow', 'green']) {
            this.lights[color] = new St.Widget({style_class: `atl-light atl-light-${color}`});
            this.frame.add_child(this.lights[color]);
        }

        this.info = new St.BoxLayout({vertical: true, style_class: 'atl-info'});
        this.state = new St.Label({style_class: 'atl-state'});
        this.detail = new St.Label({style_class: 'atl-detail'});
        this.info.add_child(this.state);
        this.info.add_child(this.detail);
        this.body.add_child(this.info);

        this.actor.connect('notify::hover', () => {
            this._syncHover();
        });
        this.actor.connect('button-press-event', (_a, event) => this._onPress(event));
        this.actor.connect('motion-event', (_a, event) => this._onMotion(event));
        this.actor.connect('leave-event', () => setCursor(this.actor));

        this.owner.layer.add_child(this.actor);
        this._place(index);
        this.update(item);
    }

    destroy() {
        this.actor.destroy();
    }

    _syncHover() {
        showButtons(this.buttons, this.actor.hover && !this.drag);
    }

    _place(index) {
        const g = this.owner.store.geometry(this.key);
        let [x, y, width, height] = [0, 0, 220, 82];
        if (g) {
            [x, y] = onStage(g.x, g.y);
            width = Math.max(48, g.width);
            height = Math.max(24, g.height);
        } else {
            x = 18 + (index % 3) * (width + 12);
            y = 42 + Math.floor(index / 3) * (height + 12);
        }
        this.actor.set_position(x, y);
        this.actor.set_size(width, height);
    }

    update(item) {
        this.item = item;
        this.title.text = item.label;
        this.state.text = STATE_TEXT[item.state] ?? item.state;
        this.detail.text = item.detail || item.key;
        this._layout();
        this._applyLights();
    }

    animate() {
        this.tick++;
        this._applyLights();
    }

    _layout() {
        const width = this.actor.width;
        const height = this.actor.height;
        const hideInfo = width < 150 || height < 58;
        const micro = width < 64 || height < 34;

        this.titleRow.visible = !micro;
        this.title.visible = width >= 118 && height >= 42;
        this.info.visible = !hideInfo;

        const chromeW = micro ? 10 : hideInfo ? 24 : 36;
        const chromeH = micro ? 8 : hideInfo ? 18 : 32;
        const lightSize = Math.max(6, Math.min(140, Math.floor(Math.min(
            Math.max(12, width - chromeW) / 4,
            Math.max(10, height - chromeH) * 0.82))));
        const spacing = Math.max(2, Math.min(10, Math.floor(lightSize / 5)));
        this.frame.style = `spacing:${spacing}px;`;
        for (const light of Object.values(this.lights))
            light.set_size(lightSize, lightSize);
    }

    _applyLights() {
        const lit = litColor(this.item.state, this.tick);
        if (this._lit !== undefined && this._lit === lit)
            return;
        this._lit = lit;
        for (const [color, light] of Object.entries(this.lights)) {
            if (color === lit)
                light.add_style_class_name('on');
            else
                light.remove_style_class_name('on');
        }
    }

    _edgeAt(x, y) {
        const w = this.actor.width;
        const h = this.actor.height;
        const edge = clamp(Math.floor(Math.min(w, h) / 8), 5, 10);
        const left = x <= edge;
        const right = x >= w - edge;
        const top = y <= edge;
        const bottom = y >= h - edge;
        if (top && left) return 'nw';
        if (top && right) return 'ne';
        if (bottom && left) return 'sw';
        if (bottom && right) return 'se';
        if (top) return 'n';
        if (bottom) return 's';
        if (left) return 'w';
        if (right) return 'e';
        return null;
    }

    // 边缘 = 缩放；其余任意位置 = 移动。
    _hitMode(x, y) {
        const edge = this._edgeAt(x, y);
        return edge ? {type: 'resize', edge} : {type: 'move', edge: null};
    }

    _onPress(event) {
        if (event.get_button() !== 1)
            return Clutter.EVENT_PROPAGATE;
        const [stageX, stageY] = event.get_coords();
        const now = event.get_time();
        if (this.lastClick &&
            now - this.lastClick.time < 360 &&
            Math.abs(stageX - this.lastClick.x) < 8 &&
            Math.abs(stageY - this.lastClick.y) < 8) {
            this.lastClick = null;
            this.owner.togglePanel(true);
            return Clutter.EVENT_STOP;
        }
        this.lastClick = {time: now, x: stageX, y: stageY};
        const [ok, actorX, actorY] = this.actor.transform_stage_point(stageX, stageY);
        if (!ok)
            return Clutter.EVENT_STOP;
        const mode = this._hitMode(actorX, actorY);
        const [x, y] = this.actor.get_position();
        this.drag = {
            type: mode.type,
            edge: mode.edge,
            stageX,
            stageY,
            x,
            y,
            width: this.actor.width,
            height: this.actor.height,
        };
        this.owner.dragController.begin(
            this,
            mode.type === 'move' ? CURSOR_NAMES.move : cursorForEdge(mode.edge));
        return Clutter.EVENT_STOP;
    }

    _onMotion(event) {
        if (this.drag)
            return Clutter.EVENT_PROPAGATE;
        const [stageX, stageY] = event.get_coords();
        const [ok, actorX, actorY] = this.actor.transform_stage_point(stageX, stageY);
        if (!ok)
            return Clutter.EVENT_PROPAGATE;
        const mode = this._hitMode(actorX, actorY);
        setCursor(this.actor, mode.type === 'move' ? CURSOR_NAMES.move : cursorForEdge(mode.edge));
        return Clutter.EVENT_PROPAGATE;
    }

    dragMotion(stageX, stageY) {
        if (!this.drag)
            return;
        const dx = stageX - this.drag.stageX;
        const dy = stageY - this.drag.stageY;
        let {x, y, width, height} = this.drag;
        if (this.drag.type === 'move') {
            x += dx;
            y += dy;
        } else {
            const edge = this.drag.edge;
            let newWidth = width;
            let newHeight = height;
            if (edge.includes('e'))
                newWidth = width + dx;
            if (edge.includes('w'))
                newWidth = width - dx;
            if (edge.includes('s'))
                newHeight = height + dy;
            if (edge.includes('n'))
                newHeight = height - dy;

            newWidth = Math.max(48, Math.round(newWidth));
            newHeight = Math.max(24, Math.round(newHeight));

            if (edge.includes('w'))
                x += width - newWidth;
            if (edge.includes('n'))
                y += height - newHeight;

            width = newWidth;
            height = newHeight;
        }
        this.actor.set_position(x, y);
        this.actor.set_size(width, height);
        this._layout();
    }

    endDrag() {
        const start = this.drag;
        this.drag = null;
        setCursor(this.actor);
        this._syncHover();
        const [x, y] = this.actor.get_position();
        const {width, height} = this.actor;
        if (!start || x !== start.x || y !== start.y || width !== start.width || height !== start.height)
            this.owner.store.setGeometry(this.key, x, y, width, height);
    }

    cancelDrag() {
        this.drag = null;
        setCursor(this.actor);
        this._syncHover();
    }
}

// 最小化：每个可见代理一盏小灯排成一行。悬停出现还原按钮；可拖动；双击开控制面板。
class MiniBar {
    constructor(owner) {
        this.owner = owner;
        this.tick = 0;
        this.drag = null;
        this.lastClick = null;
        this.dots = new Map();

        this.actor = new St.BoxLayout({
            reactive: true,
            visible: false,
            track_hover: true,
            style_class: 'atl-mini',
        });
        this.row = new St.BoxLayout({style_class: 'atl-mini-row'});
        this.buttons = new St.BoxLayout({style_class: 'atl-btns'});
        showButtons(this.buttons, false);
        this.buttons.add_child(makeButton('+', () => owner.setMinimized(false)));
        this.actor.add_child(this.row);
        this.actor.add_child(this.buttons);

        this.actor.connect('notify::hover', () => {
            this._syncHover();
        });
        this.actor.connect('button-press-event', (_a, event) => this._onPress(event));
        owner.layer.add_child(this.actor);

        const p = owner.store.miniPos();
        const [x, y] = onStage(p?.x ?? 18, p?.y ?? 42);
        this.actor.set_position(x, y);
    }

    _syncHover() {
        showButtons(this.buttons, (this.actor.hover && !this.drag) || this.dots.size === 0);
    }

    update(items) {
        const seen = new Set();
        for (const item of items) {
            seen.add(item.key);
            let dot = this.dots.get(item.key);
            if (!dot) {
                dot = {item, widget: new St.Widget({style_class: 'atl-dot'})};
                this.dots.set(item.key, dot);
                this.row.add_child(dot.widget);
            }
            dot.item = item;
        }
        for (const [key, dot] of this.dots) {
            if (!seen.has(key)) {
                dot.widget.destroy();
                this.dots.delete(key);
            }
        }
        this._apply();
        this._syncHover();
    }

    animate() {
        this.tick++;
        this._apply();
    }

    // 熄灭的一拍保留颜色（暗色），一行不跳动。
    _apply() {
        for (const dot of this.dots.values()) {
            const {item, widget} = dot;
            const lit = litColor(item.state, this.tick);
            const base = lit ?? (item.state === 'working' ? 'green' : 'yellow');
            if (dot.sig === `${base}|${lit}`)
                continue;
            dot.sig = `${base}|${lit}`;
            for (const c of ['red', 'yellow', 'green'])
                widget.remove_style_class_name(`atl-dot-${c}`);
            widget.add_style_class_name(`atl-dot-${base}`);
            if (lit)
                widget.add_style_class_name('on');
            else
                widget.remove_style_class_name('on');
        }
    }

    _onPress(event) {
        if (event.get_button() !== 1)
            return Clutter.EVENT_PROPAGATE;
        const [stageX, stageY] = event.get_coords();
        const now = event.get_time();
        if (this.lastClick && now - this.lastClick < 360) {
            this.lastClick = null;
            this.owner.togglePanel(true);
            return Clutter.EVENT_STOP;
        }
        this.lastClick = now;
        const [x, y] = this.actor.get_position();
        this.drag = {stageX, stageY, x, y};
        this.owner.dragController.begin(this, CURSOR_NAMES.move);
        return Clutter.EVENT_STOP;
    }

    dragMotion(stageX, stageY) {
        if (!this.drag)
            return;
        const [x, y] = onStage(
            this.drag.x + stageX - this.drag.stageX,
            this.drag.y + stageY - this.drag.stageY);
        this.actor.set_position(x, y);
    }

    endDrag() {
        const start = this.drag;
        this.drag = null;
        this._syncHover();
        const [x, y] = this.actor.get_position();
        if (!start || x !== start.x || y !== start.y)
            this.owner.store.setMiniPos(x, y);
    }

    cancelDrag() {
        this.drag = null;
        this._syncHover();
    }
}

// 控制面板：逐个代理显示 / 隐藏。
class Panel {
    constructor(owner) {
        this.owner = owner;
        this.drag = null;
        this.rows = new Map();
        this.actor = new St.BoxLayout({
            vertical: true,
            reactive: true,
            visible: false,
            style_class: 'atl-panel',
        });
        this.actor.set_position(80, 80);
        this.header = new St.BoxLayout({style_class: 'atl-panel-header', reactive: true});
        this.title = new St.Label({
            text: 'Agent status',
            style_class: 'atl-panel-title',
            x_expand: true,
        });
        this.close = new St.Label({text: '×', style_class: 'atl-panel-close', reactive: true});
        this.close.connect('button-press-event', () => {
            this.toggle(false);
            return Clutter.EVENT_STOP;
        });
        this.header.connect('button-press-event', (_a, event) => this._onPress(event));
        this.header.connect('motion-event', () => {
            setCursor(this.header, CURSOR_NAMES.move);
            return Clutter.EVENT_PROPAGATE;
        });
        this.header.connect('leave-event', () => setCursor(this.header));
        this.header.add_child(this.title);
        this.header.add_child(this.close);
        this.actor.add_child(this.header);

        this.scroll = new St.ScrollView({
            style_class: 'atl-panel-scroll',
            overlay_scrollbars: true,
            x_expand: true,
            y_expand: true,
        });
        this.scroll.set_policy(St.PolicyType.NEVER, St.PolicyType.AUTOMATIC);
        this.list = new St.BoxLayout({vertical: true, style_class: 'atl-panel-list', x_expand: true});
        this.scroll.set_child(this.list);
        this.actor.add_child(this.scroll);

        this._fitToStage();
        owner.layer.add_child(this.actor);
    }

    _onPress(event) {
        if (event.get_button() !== 1)
            return Clutter.EVENT_PROPAGATE;
        const [stageX, stageY] = event.get_coords();
        const [x, y] = this.actor.get_position();
        this.drag = {stageX, stageY, x, y};
        this.owner.dragController.begin(this, CURSOR_NAMES.move);
        raiseActor(this.actor);
        return Clutter.EVENT_STOP;
    }

    dragMotion(stageX, stageY) {
        if (!this.drag)
            return;
        this.actor.set_position(
            this.drag.x + stageX - this.drag.stageX,
            this.drag.y + stageY - this.drag.stageY);
        this._fitToStage(false);
    }

    endDrag() {
        this.drag = null;
        setCursor(this.header);
    }

    cancelDrag() {
        this.drag = null;
        setCursor(this.header);
    }

    toggle(show = null) {
        this.actor.visible = show === null ? !this.actor.visible : show;
        if (this.actor.visible) {
            this._fitToStage();
            raiseActor(this.actor);
        }
    }

    update(items) {
        const seen = new Set();
        for (const item of items) {
            seen.add(item.key);
            let row = this.rows.get(item.key);
            if (!row) {
                row = this._createRow(item);
                this.rows.set(item.key, row);
            }
            row._name.text = item.label;
            row._detail.text = `${STATE_TEXT[item.state] ?? item.state}  ${item.detail ?? ''}`;
            if (this.owner.store.visible(item.key))
                row._check.add_style_class_name('checked');
            else
                row._check.remove_style_class_name('checked');
        }
        for (const [key, row] of this.rows) {
            if (!seen.has(key)) {
                row.destroy();
                this.rows.delete(key);
            }
        }
    }

    _fitToStage(resize = true) {
        const margin = 32;
        const stageW = Math.max(320, global.stage.width);
        const stageH = Math.max(240, global.stage.height);
        const width = Math.max(300, Math.min(520, stageW - margin * 2));
        const height = Math.max(180, Math.min(460, stageH - margin * 2));
        if (resize)
            this.actor.set_size(width, height);
        const x = clamp(this.actor.x, margin, Math.max(margin, stageW - width - margin));
        const y = clamp(this.actor.y, margin, Math.max(margin, stageH - height - margin));
        this.actor.set_position(x, y);
        this.scroll.set_size(this.actor.width - 24, this.actor.height - 56);
    }

    _createRow(item) {
        const row = new St.BoxLayout({style_class: 'atl-panel-row', reactive: true});
        const check = new St.Widget({style_class: 'atl-checkbox', reactive: true});
        const labels = new St.BoxLayout({vertical: true, x_expand: true});
        row._name = new St.Label({style_class: 'atl-panel-label'});
        row._detail = new St.Label({style_class: 'atl-panel-detail'});
        row._check = check;
        labels.add_child(row._name);
        labels.add_child(row._detail);
        row.add_child(check);
        row.add_child(labels);
        row.connect('button-press-event', () => {
            this.owner.setVisible(item.key, !this.owner.store.visible(item.key));
            return Clutter.EVENT_STOP;
        });
        this.list.add_child(row);
        return row;
    }
}

export default class AgentTrafficLightExtension extends Extension {
    enable() {
        this.store = new Store();
        this.actors = new Map();
        this._lastItems = null;
        this._checkAllHidden = true;

        this.layer = new St.Widget({
            reactive: false,
            x_expand: true,
            y_expand: true,
            clip_to_allocation: false,
        });
        Main.layoutManager.addChrome(this.layer, {
            affectsStruts: false,
            trackFullscreen: true,
        });
        this._resizeLayer();
        this._stageSizeId = global.stage.connect('notify::allocation', () => this._resizeLayer());

        this.dragController = new DragController(this);
        this.panel = new Panel(this);
        this.mini = new MiniBar(this);
        this._refresh();
        this._refreshId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._animateId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            for (const actor of this.actors.values())
                actor.animate();
            this.mini.animate();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        for (const id of [this._refreshId, this._animateId]) {
            if (id)
                GLib.Source.remove(id);
        }
        this._refreshId = this._animateId = null;
        if (this._stageSizeId) {
            global.stage.disconnect(this._stageSizeId);
            this._stageSizeId = null;
        }
        // 先收掉指针抓取，再销毁整层（子 actor 连同其信号一起释放）。
        this.dragController?.destroy();
        this.dragController = null;
        if (this.layer) {
            Main.layoutManager.removeChrome(this.layer);
            this.layer.destroy();
            this.layer = null;
        }
        this.actors?.clear();
        this.actors = this.panel = this.mini = this.store = this._lastItems = null;
    }

    _resizeLayer() {
        this.layer.set_position(0, 0);
        this.layer.set_size(global.stage.width, global.stage.height);
    }

    // 读状态文件；缺失 / 损坏 / updated_at 超过 STALE_SEC → 全部按 unknown（红灯）。
    _readItems() {
        const data = readJson(STATUS_PATH, null);
        let ts = NaN;
        if (data && typeof data === 'object' && Array.isArray(data.agents)) {
            ts = parseTimestamp(data.updated_at);
            this._lastItems = data.agents
                .filter(a => a && typeof a.key === 'string' && a.key && typeof a.state === 'string')
                .map(a => ({
                    key: a.key,
                    label: String(a.label || a.key),
                    state: a.state,
                    detail: a.detail == null ? '' : String(a.detail),
                }));
        }
        let items = this._lastItems ?? [];
        if (!(Date.now() / 1000 - ts <= STALE_SEC)) {
            items = items.map(i => ({...i, state: 'unknown', detail: 'status file stale'}));
            if (!items.length)
                items = [{key: 'status', label: 'Status', state: 'unknown', detail: 'no status file'}];
        }
        return items;
    }

    _refresh() {
        const items = this._readItems();
        if (this._checkAllHidden) {
            this._checkAllHidden = false;
            // 全被 × 掉了：重新启用扩展时恢复显示。
            if (!items.some(i => this.store.visible(i.key)))
                this.store.showAll();
        }
        const minimized = this.store.minimized;
        const visible = items.filter(i => this.store.visible(i.key));
        const seen = new Set();
        items.forEach((item, index) => {
            seen.add(item.key);
            let actor = this.actors.get(item.key);
            if (!this.store.visible(item.key) || minimized) {
                actor?.actor.hide();
                return;
            }
            if (!actor) {
                actor = new AgentActor(this, item, index);
                this.actors.set(item.key, actor);
            }
            actor.actor.show();
            if (!actor.drag)
                actor.update(item);
        });
        for (const [key, actor] of this.actors) {
            if (!seen.has(key)) {
                actor.destroy();
                this.actors.delete(key);
            }
        }
        this.mini.actor.visible = minimized;
        this.mini.update(visible);
        this.panel.update(items);
    }

    setVisible(key, visible) {
        this.store.setVisible(key, visible);
        this._refresh();
    }

    setMinimized(minimized) {
        this.store.minimized = minimized;
        // 最小化时全被隐藏：还原同时恢复显示，保证总有路回来。
        if (!minimized && !(this._lastItems ?? []).some(i => this.store.visible(i.key)))
            this.store.showAll();
        this._refresh();
    }

    togglePanel(show = null) {
        this.panel.toggle(show);
    }
}
