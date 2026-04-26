import tkinter as tk
from tkinter import ttk
import random, time, os, ctypes, platform, configparser
from typing import List, Dict, Any, Callable, Optional

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "settings.ini")

def save_settings_to_file(channels: List[Dict], pressures: List[Dict]):
    conf = configparser.ConfigParser()
    for i, ch in enumerate(channels):
        conf[f"CH{i+1}"] = {k: str(ch[k]) for k in ['name','mode','sv','limit_h','limit_l','p','i','d']}
    for i, pr in enumerate(pressures):
        conf[f"PRESS{i+1}"] = {k: str(pr[k]) for k in ['name','unit','min','max']}
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f: conf.write(f)

def load_settings_from_file(channels: List[Dict], pressures: List[Dict]):
    if not os.path.exists(CONFIG_FILE): return
    conf = configparser.ConfigParser()
    conf.read(CONFIG_FILE, encoding='utf-8')
    for i, ch in enumerate(channels):
        sec = f"CH{i+1}"
        if sec in conf:
            ch['name'] = conf.get(sec, 'name', fallback=ch['name'])
            ch['mode'] = conf.get(sec, 'mode', fallback='CONTROL')
            for k in ['sv','limit_h','limit_l','p','i','d']:
                ch[k] = conf.getfloat(sec, k, fallback=float(ch[k]))
    for i, pr in enumerate(pressures):
        sec = f"PRESS{i+1}"
        if sec in conf:
            pr['name'] = conf.get(sec, 'name', fallback=pr['name'])
            pr['unit'] = conf.get(sec, 'unit', fallback='kPa.abs')
            for k in ['min','max']: pr[k] = conf.getfloat(sec, k, fallback=float(pr[k]))

def load_font(path):
    if platform.system() == "Windows" and os.path.exists(path):
        ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0)
load_font(os.path.join(os.path.dirname(__file__), "DSEG7Modern-BoldItalic.ttf"))

try:
    import RPi.GPIO as GPIO
except ImportError:
    class DummyGPIO:
        BCM, OUT, IN, HIGH, LOW, PUD_UP = 'BCM','OUT','IN',True,False,'PUD_UP'
        def setmode(self, _m): pass
        def setup(self, _p, _m, pull_up_down=None): pass
        def output(self, _p, _v): pass
        def input(self, _p): return True
    GPIO = DummyGPIO()

PIN_DRIVE, PIN_PUMP, PIN_CELL = 17, 27, 22
PIN_STOP_CH, PIN_SSR_CH = [23,24,25,8], [5,6,12,13]
PIN_ALM_ALL = 26
GPIO.setmode(GPIO.BCM)
for p in [PIN_DRIVE, PIN_PUMP, PIN_CELL] + PIN_SSR_CH: GPIO.setup(p, GPIO.OUT); GPIO.output(p, GPIO.LOW)
for p in PIN_STOP_CH + [PIN_ALM_ALL]: GPIO.setup(p, GPIO.IN, pull_up_down=GPIO.PUD_UP)

class Keypad(tk.Toplevel):
    def __init__(self, master, target_var, title=""):
        super().__init__(master)
        self.target_var, self.first_press = target_var, True
        self.geometry("400x520+312+40"); self.configure(bg='#333'); self.grab_set()
        self.val_str = tk.StringVar(value=str(target_var.get()))
        tk.Label(self, textvariable=self.val_str, font=("Helvetica", 32), bg="#000", fg="#0F0", anchor="e", padx=20).pack(fill=tk.X, pady=10)
        btn_f = tk.Frame(self, bg='#333'); btn_f.pack(fill=tk.BOTH, expand=1)
        btns = ['7','8','9','4','5','6','1','2','3','0','.','CL']
        for i, b in enumerate(btns):
            tk.Button(btn_f, text=b, font=("Helvetica", 18, "bold"), command=lambda t=b: self.press(t)).grid(row=i//3, column=i%3, sticky="nsew", padx=2, pady=2)
        for i in range(4): btn_f.grid_rowconfigure(i, weight=1)
        for i in range(3): btn_f.grid_columnconfigure(i, weight=1)
        tk.Button(self, text="決定", font=("Helvetica", 18, "bold"), bg="#080", fg="white", height=2, command=self.save).pack(fill=tk.X, padx=10, pady=10)

    def press(self, char):
        if char == "CL": self.val_str.set(""); self.first_press = False; return
        curr = "" if self.first_press else self.val_str.get()
        self.first_press = False
        if char == "." and "." in curr: return
        self.val_str.set(curr + char)

    def save(self):
        try:
            v = self.val_str.get()
            self.target_var.set(float(v if v not in ["","."] else 0)); self.destroy()
        except: pass

class WaveshareApp:
    def __init__(self, master: tk.Tk):
        self.master = master
        self.master.title("Controller"); self.master.geometry("1024x600"); self.master.configure(bg='#1A1A1A')
        t_n, p_n = ["水温","天板温度","気相温度","出口温度"], ["セル入口圧力","セル出口圧力","背圧弁圧力","加湿器圧力"]
        self.channels = [{'id':i,'name':t_n[i],'mode':'CONTROL','run_state':False,'alm':False,'pv':25.0,'sv':100.0,'mv':0.0,'limit_h':180.0,'limit_l':-50.0,'p':2.0,'i':0.1,'d':0.0,'integral':0.0,'last_error':0.0,'at_active':False,'at_data':[]} for i in range(4)]
        self.pressures = [{'id':i,'name':p_n[i],'val':101.3,'min':0.0,'max':500.0,'unit':'kPa.abs'} for i in range(4)]
        load_settings_from_file(self.channels, self.pressures)
        self.btn_states = {'drive':False, 'pump':False, 'cell':False}
        self.pv_labels, self.mv_labels, self.led_canvases, self.run_labels, self.at_status_labels, self.alm_labels, self.p_val_labels = [],[],[],[],[],[],[]
        self.settings_win = None; self.setting_at_btns, self.setting_at_cancel_btns = [], []
        self.create_widgets(); self.update_loop()

    def create_widgets(self):
        main_fr = tk.Frame(self.master, bg='#1A1A1A'); main_fr.pack(fill=tk.BOTH, expand=1, padx=5, pady=5)
        for i in range(4): main_fr.grid_columnconfigure(i, weight=1, minsize=250)
        for r, w in [(0,4),(1,3),(2,2)]: main_fr.grid_rowconfigure(r, weight=w)
        for i in range(4):
            ch = self.channels[i]; f = tk.Frame(main_fr, bg='#2B2B2B', bd=2, relief=tk.RIDGE); f.grid(row=0, column=i, sticky='nsew', padx=2, pady=2)
            tk.Label(f, text=ch['name'], fg='#FFF', bg='#404040', font=("Helvetica", 11, "bold")).pack(fill=tk.X)
            st = tk.Frame(f, bg='#2B2B2B'); st.pack(fill=tk.X, padx=5)
            rl, atl, al = tk.Label(st, text="RUN", fg='#333', bg='#2B2B2B', font=("Helvetica", 9, "bold")), tk.Label(st, text="AT中", fg='#333', bg='#2B2B2B', font=("Helvetica", 9, "bold")), tk.Label(st, text="ALM", fg='#333', bg='#2B2B2B', font=("Helvetica", 9, "bold"))
            rl.pack(side=tk.LEFT); atl.pack(side=tk.LEFT, expand=1); al.pack(side=tk.RIGHT)
            self.run_labels.append(rl); self.at_status_labels.append(atl); self.alm_labels.append(al)
            pv_fr = tk.Frame(f, bg='#000', relief=tk.SUNKEN, bd=1); pv_fr.pack(fill=tk.BOTH, expand=1, padx=5, pady=2)
            tk.Label(pv_fr, text="888.8", fg='#200', bg='#000', font=("DSEG7 Modern", 40, "italic")).place(relx=0.4, rely=0.5, anchor="center")
            pvl = tk.Label(pv_fr, text="--.-", fg='#F33', bg='#000', font=("DSEG7 Modern", 40, "italic")); pvl.place(relx=0.4, rely=0.5, anchor="center")
            tk.Label(pv_fr, text="℃", fg='#F33', bg='#000', font=("Helvetica", 12, "bold")).pack(side=tk.RIGHT, anchor="s", pady=5); self.pv_labels.append(pvl)
            mv_fr = tk.Frame(f, bg='#2B2B2B'); mv_fr.pack(fill=tk.X, padx=5, pady=2)
            mvl = tk.Label(mv_fr, text="0.0", fg='#F80', bg='#000', font=("DSEG7 Modern", 18, "italic"), width=5); mvl.pack(side=tk.LEFT)
            tk.Label(mv_fr, text="%", fg='#F80', bg='#2B2B2B', font=("Helvetica", 12, "bold")).pack(side=tk.LEFT)
            lc = tk.Canvas(mv_fr, width=15, height=15, bg='#2B2B2B', highlightthickness=0); lc.pack(side=tk.RIGHT, padx=2)
            lc.create_oval(2, 2, 13, 13, fill='#400', tags="led"); self.mv_labels.append(mvl); self.led_canvases.append(lc)
        for i in range(4):
            f = tk.Frame(main_fr, bg='#2B2B2B', bd=2, relief=tk.RIDGE); f.grid(row=1, column=i, sticky='nsew', padx=2, pady=2)
            tk.Label(f, text=self.pressures[i]['name'], fg='#FFF', bg='#404040', font=("Helvetica", 11, "bold")).pack(fill=tk.X)
            p_dis = tk.Frame(f, bg='#000', relief=tk.SUNKEN, bd=1); p_dis.pack(fill=tk.BOTH, expand=1, padx=10, pady=5)
            tk.Label(p_dis, text="888.8", fg='#020', bg='#000', font=("DSEG7 Modern", 40, "italic")).place(relx=0.5, rely=0.4, anchor="center")
            pvl = tk.Label(p_dis, text="----", fg='#0F0', bg='#000', font=("DSEG7 Modern", 40, "italic")); pvl.place(relx=0.5, rely=0.4, anchor="center")
            tk.Label(p_dis, text=self.pressures[i]['unit'], fg='#0F0', bg='#000', font=("Helvetica", 12, "bold")).pack(side=tk.BOTTOM, pady=2); self.p_val_labels.append(pvl)
        btn_fr = tk.Frame(main_fr, bg='#1A1A1A'); btn_fr.grid(row=2, column=0, columnspan=4, sticky='nsew')
        for i in range(5): btn_fr.grid_columnconfigure(i, weight=1, uniform="bg")
        btn_fr.grid_rowconfigure(0, weight=1)
        self.b_drive = tk.Button(btn_fr, text="運転\nOFF", bg='#444', fg='white', font=("Helvetica", 16, "bold"), command=self.toggle_drive); self.b_drive.grid(row=0, column=0, sticky='nsew', padx=2, pady=5)
        self.b_pump = tk.Button(btn_fr, text="ポンプ\nOFF", bg='#444', fg='white', font=("Helvetica", 16, "bold"), command=lambda: self.toggle('pump', PIN_PUMP, self.b_pump, "ポンプ")); self.b_pump.grid(row=0, column=1, sticky='nsew', padx=2, pady=5)
        self.b_cell = tk.Button(btn_fr, text="セル\nOFF", bg='#444', fg='white', font=("Helvetica", 16, "bold"), command=lambda: self.toggle('cell', PIN_CELL, self.b_cell, "セル")); self.b_cell.grid(row=0, column=2, sticky='nsew', padx=2, pady=5)
        tk.Button(btn_fr, text="設定", bg='#BBB', font=("Helvetica", 16, "bold"), command=self.open_settings).grid(row=0, column=3, sticky='nsew', padx=2, pady=5)
        tk.Button(btn_fr, text="終了", bg='#822', fg='white', font=("Helvetica", 16, "bold"), command=self.exit_app).grid(row=0, column=4, sticky='nsew', padx=2, pady=5)

    def toggle_drive(self):
        self.btn_states['drive'] = not self.btn_states['drive']; s = self.btn_states['drive']
        self.b_drive.configure(bg='#080' if s else '#444', text=f"運転\n{'ON' if s else 'OFF'}"); GPIO.output(PIN_DRIVE, GPIO.HIGH if s else GPIO.LOW)
        for ch in self.channels: 
            ch['run_state'] = s
            if not s: ch['at_active'] = False

    def toggle(self, k, p, o, t):
        self.btn_states[k] = not self.btn_states[k]; s = self.btn_states[k]
        o.configure(bg='#080' if s else '#444', text=f"{t}\n{'ON' if s else 'OFF'}"); GPIO.output(p, GPIO.HIGH if s else GPIO.LOW)

    def update_loop(self):
        ext_alm = GPIO.input(PIN_ALM_ALL) == GPIO.LOW
        drive_on = self.btn_states['drive']
        if self.settings_win and self.settings_win.winfo_exists():
            for i, b in enumerate(self.setting_at_btns): b.config(state="disabled" if self.channels[i]['at_active'] else ("normal" if drive_on else "disabled"), bg="#A44" if self.channels[i]['at_active'] else "#444")
            for i, c in enumerate(self.setting_at_cancel_btns): c.config(state="normal" if self.channels[i]['at_active'] else "disabled")
        for i, ch in enumerate(self.channels):
            is_run = ch['run_state'] and GPIO.input(PIN_STOP_CH[i]) == GPIO.HIGH and not ch['alm']
            pv, sv = float(ch['pv']), float(ch['sv'])
            ch['alm'] = (pv > float(ch['limit_h']) or pv < float(ch['limit_l']) or ext_alm)
            if ch['at_active'] and is_run:
                ch['mv'] = 100.0 if pv < sv else 0.0
                if not ch['at_data'] or (ch['at_data'][-1][1] < sv <= pv) or (ch['at_data'][-1][1] > sv >= pv): ch['at_data'].append((time.time(), pv))
                if len(ch['at_data']) >= 7:
                    t_diff = ch['at_data'][-1][0] - ch['at_data'][-5][0]
                    ch['p'], ch['i'], ch['d'] = round(60.0/t_diff, 2), round(t_diff*0.5, 2), round(t_diff*0.12, 2); ch['at_active'] = False; save_settings_to_file(self.channels, self.pressures)
                ch['pv'] += (0.8 if ch['mv']>0 else -0.4) + random.uniform(-0.1, 0.1)
            elif ch['mode'] == 'CONTROL' and is_run:
                err = sv - pv; ch['integral'] += err * 0.5; deriv = (err - ch['last_error']) / 0.5; ch['last_error'] = err
                ch['mv'] = max(0.0, min(100.0, (float(ch['p'])*err + float(ch['i'])*ch['integral'] + float(ch['d'])*deriv)))
            else:
                ch['mv'], ch['integral'], ch['at_active'] = 0.0, 0.0, False; ch['pv'] += random.uniform(-0.1, 0.1) if ch['mode'] == 'MONITOR' else (25.0 - pv)*0.02
            self.pv_labels[i].config(text=f"{float(ch['pv']):5.1f}"); self.mv_labels[i].config(text=f"{float(ch['mv']):5.1f}")
            self.run_labels[i].config(fg='#0F0' if is_run else '#333'); self.at_status_labels[i].config(fg='#FF0' if ch['at_active'] else '#333')
            self.alm_labels[i].config(fg='#F00' if ch['alm'] else '#333')
            on = float(ch['mv']) > (time.time() % 2.0 / 2.0 * 100) and is_run
            self.led_canvases[i].itemconfig("led", fill='#F00' if on else '#400'); GPIO.output(PIN_SSR_CH[i], GPIO.HIGH if on else GPIO.LOW)
        for i, pr in enumerate(self.pressures): pr['val'] += random.uniform(-0.3, 0.3); self.p_val_labels[i].config(text=f"{float(pr['val']):5.1f}")
        self.master.after(500, self.update_loop)

    def open_settings(self):
        win = tk.Toplevel(self.master); win.geometry("1024x600+0+0"); win.grab_set(); self.settings_win = win
        ttk.Style().configure("TNotebook.Tab", font=("Helvetica", 16), padding=[20,10])
        nb = ttk.Notebook(win); t_t, p_t = ttk.Frame(nb), ttk.Frame(nb)
        nb.add(t_t, text='  温調設定  '); nb.add(p_t, text='  圧力設定  '); nb.pack(fill="both", expand=1)
        for i in range(4): t_t.grid_columnconfigure(i, weight=1, uniform="tc"); p_t.grid_columnconfigure(i, weight=1, uniform="pc")
        save_refs, self.setting_at_btns, self.setting_at_cancel_btns = [], [], []
        def v_row(par, var, row, lbl):
            fr = tk.Frame(par, bg="#EEE"); fr.grid(row=row, column=0, sticky="ew", pady=2)
            tk.Label(fr, text=lbl, font=("Helvetica", 10), width=6, anchor="w").pack(side=tk.LEFT, padx=5)
            e = tk.Entry(fr, textvariable=var, font=("Helvetica", 14, "bold"), width=8, justify="center")
            e.pack(side=tk.RIGHT, expand=1, fill=tk.X, padx=5, ipady=5); e.bind("<Button-1>", lambda ev: Keypad(win, var, lbl))
        for i in range(4):
            ch = self.channels[i]; f = tk.LabelFrame(t_t, text=ch['name'], font=("Helvetica", 11, "bold")); f.grid(row=0, column=i, padx=5, pady=5, sticky='nsew')
            m_v = tk.StringVar(value=str(ch['mode']))
            mb = tk.Button(f, text=m_v.get(), font=("Helvetica", 12, "bold"), fg="white", bg="#468" if m_v.get()=="CONTROL" else "#864")
            mb.grid(row=0, column=0, sticky="ew", pady=5); mb.config(command=lambda v=m_v, b=mb: [v.set("MONITOR" if v.get()=="CONTROL" else "CONTROL"), b.config(text=v.get(), bg="#468" if v.get()=="CONTROL" else "#864")])
            sv, lh, ll, p, iv, d = [tk.DoubleVar(value=float(ch[k])) for k in ['sv','limit_h','limit_l','p','i','d']]
            for idx, (v, l) in enumerate(zip([sv,lh,ll,p,iv,d], ["SV:","上限:","下限:","P:","I:","D:"])): v_row(f, v, idx+1, l)
            def trigger_at(idx=i): self.channels[idx].update({'at_active':True,'at_data':[]})
            def cancel_at(idx=i): self.channels[idx].update({'at_active':False})
            at_b, at_c = tk.Button(f, text="AT開始", font=("Helvetica", 10, "bold"), bg="#444", fg="white", command=trigger_at), tk.Button(f, text="中断", font=("Helvetica", 10, "bold"), bg="#444", fg="white", command=cancel_at)
            at_b.grid(row=7, column=0, sticky="ew", pady=2); at_c.grid(row=8, column=0, sticky="ew", pady=2)
            self.setting_at_btns.append(at_b); self.setting_at_cancel_btns.append(at_c)
            save_refs.append(lambda idx=i, mv=m_v, s_v=sv, l_h=lh, l_l=ll, p_v=p, i_v=iv, d_v=d: {'type':'ch','id':idx,'mode':mv.get(),'sv':s_v.get(),'limit_h':l_h.get(),'limit_l':l_l.get(),'p':p_v.get(),'i':i_v.get(),'d':d_v.get()})
        for i in range(4):
            pr = self.pressures[i]; f = tk.LabelFrame(p_t, text=pr['name'], font=("Helvetica", 11, "bold")); f.grid(row=0, column=i, padx=5, pady=5, sticky='nsew')
            u_v = tk.StringVar(value=str(pr['unit'])); ub = tk.Button(f, text=u_v.get(), font=("Helvetica", 10, "bold"), command=lambda v=u_v, b=None: [v.set("kPaG" if v.get()=="kPa.abs" else "kPa.abs"), ub.config(text=v.get())]); ub.grid(row=0, column=0, sticky="ew", pady=5)
            mi, ma = tk.DoubleVar(value=float(pr['min'])), tk.DoubleVar(value=float(pr['max'])); v_row(f, mi, 1, "MIN:"); v_row(f, ma, 2, "MAX:")
            save_refs.append(lambda idx=i, uv=u_v, miv=mi, mav=ma: {'type':'pr','id':idx,'unit':uv.get(),'min':miv.get(),'max':mav.get()})
        btm = tk.Frame(win); btm.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        def save_act():
            for func in save_refs:
                d_o = func()
                if d_o['type'] == 'ch': self.channels[d_o['id']].update(d_o)
                else: self.pressures[d_o['id']].update(d_o)
            save_settings_to_file(self.channels, self.pressures)
        tk.Button(btm, text="設定を保存 (ini更新)", font=("Helvetica", 18, "bold"), bg='#254', fg='white', height=2, command=save_act).pack(side=tk.LEFT, expand=1, fill=tk.X, padx=5)
        tk.Button(btm, text="設定画面を閉じる", font=("Helvetica", 18, "bold"), bg='#444', fg='white', height=2, command=win.destroy).pack(side=tk.LEFT, expand=1, fill=tk.X, padx=5)

    def exit_app(self):
        save_settings_to_file(self.channels, self.pressures)
        for p in PIN_SSR_CH + [PIN_DRIVE, PIN_PUMP, PIN_CELL]: GPIO.output(p, GPIO.LOW)
        self.master.destroy()

if __name__ == "__main__":
    r = tk.Tk(); app = WaveshareApp(r); r.mainloop()
