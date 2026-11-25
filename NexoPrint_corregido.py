import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3
import csv
import shutil
import os
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# NEXOPRINT - Sistema de gestión de pedidos
# Proyecto final - Algoritmia y Programación
# Miguel Angel Alvarez Garcia
# ============================================================

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "copy_express.db"

PRECIOS = {
    "B/N": {"Carta": 200, "Oficio": 250, "A4": 200},
    "Color": {"Carta": 800, "Oficio": 1000, "A4": 800},
}
RECARGO_URGENTE = 0.20
ESTADOS = ["Pendiente", "En proceso", "Listo", "Por confirmar", "Entregado", "Cancelado"]
USUARIO_ADMIN = "admin"
CLAVE_ADMIN = "Nexoprint"
PAGOS = ["Pendiente", "Pagado"]

COLORS = {
    "navy": "#172554", "navy2": "#1e3a8a", "blue": "#2563eb",
    "blue_light": "#eff6ff", "bg": "#f4f7fb", "white": "#ffffff",
    "text": "#172033", "muted": "#64748b", "line": "#e2e8f0",
    "green": "#15803d", "green_bg": "#dcfce7", "orange": "#c2410c",
    "orange_bg": "#ffedd5", "red": "#b91c1c", "red_bg": "#fee2e2",
    "purple": "#6d28d9", "purple_bg": "#ede9fe"
}


def formato_dinero(valor):
    return "$" + f"{int(valor):,}".replace(",", ".")


def calcular_precio(copias, tipo, tamano, urgente):
    total = copias * PRECIOS[tipo][tamano]
    if urgente:
        total *= (1 + RECARGO_URGENTE)
    return int(total)


def calcular_minutos(copias, tipo, urgente):
    minutos = 5 + copias * (0.5 if tipo == "B/N" else 1)
    if urgente:
        minutos /= 2
    return max(1, int(minutos))


class BaseDatos:
    def __init__(self, ruta):
        self.conn = sqlite3.connect(ruta)
        self.conn.row_factory = sqlite3.Row
        self.crear_tabla()

    def crear_tabla(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                correo TEXT NOT NULL UNIQUE,
                clave_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'cliente'
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente TEXT NOT NULL,
                archivo TEXT NOT NULL,
                ruta_archivo TEXT,
                copias INTEGER NOT NULL,
                tipo TEXT NOT NULL,
                tamano TEXT NOT NULL,
                urgente INTEGER NOT NULL DEFAULT 0,
                precio INTEGER NOT NULL,
                minutos INTEGER NOT NULL,
                estado TEXT NOT NULL DEFAULT 'Pendiente',
                pago TEXT NOT NULL DEFAULT 'Pendiente',
                fecha TEXT NOT NULL,
                fecha_fin_estimada TEXT,
                aviso_expirado INTEGER NOT NULL DEFAULT 0,
                respuesta_cliente TEXT NOT NULL DEFAULT 'Sin respuesta',
                usuario_id INTEGER
            )
        """)
        # Migración automática para bases de datos creadas con versiones anteriores.
        columnas = {fila[1] for fila in self.conn.execute("PRAGMA table_info(pedidos)")}
        if "ruta_archivo" not in columnas:
            self.conn.execute("ALTER TABLE pedidos ADD COLUMN ruta_archivo TEXT")
        if "fecha_fin_estimada" not in columnas:
            self.conn.execute("ALTER TABLE pedidos ADD COLUMN fecha_fin_estimada TEXT")
        if "aviso_expirado" not in columnas:
            self.conn.execute("ALTER TABLE pedidos ADD COLUMN aviso_expirado INTEGER NOT NULL DEFAULT 0")
        if "respuesta_cliente" not in columnas:
            self.conn.execute("ALTER TABLE pedidos ADD COLUMN respuesta_cliente TEXT NOT NULL DEFAULT 'Sin respuesta'")
        if "usuario_id" not in columnas:
            self.conn.execute("ALTER TABLE pedidos ADD COLUMN usuario_id INTEGER")
        self.conn.commit()

    @staticmethod
    def generar_hash(clave, salt=None):
        salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), salt.encode("utf-8"), 200000).hex()
        return digest, salt

    def registrar_usuario(self, nombre, correo, clave):
        digest, salt = self.generar_hash(clave)
        try:
            cur = self.conn.execute(
                "INSERT INTO usuarios (nombre, correo, clave_hash, salt, rol) VALUES (?, ?, ?, ?, 'cliente')",
                (nombre, correo.lower(), digest, salt)
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def autenticar_usuario(self, correo, clave):
        usuario = self.conn.execute(
            "SELECT * FROM usuarios WHERE correo=?", (correo.lower(),)
        ).fetchone()
        if not usuario:
            return None
        digest, _ = self.generar_hash(clave, usuario["salt"])
        if hmac.compare_digest(digest, usuario["clave_hash"]):
            return usuario
        return None

    def todos(self, usuario_id=None):
        if usuario_id is None:
            return self.conn.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
        return self.conn.execute(
            "SELECT * FROM pedidos WHERE usuario_id=? ORDER BY id DESC", (usuario_id,)
        ).fetchall()

    def cancelar_cliente(self, pedido_id, usuario_id):
        cur = self.conn.execute(
            """UPDATE pedidos SET estado='Cancelado'
               WHERE id=? AND usuario_id=? AND estado IN ('Pendiente', 'En proceso')""",
            (pedido_id, usuario_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def obtener(self, pedido_id):
        return self.conn.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()

    def crear(self, datos, usuario_id=None):
        cur = self.conn.execute("""
            INSERT INTO pedidos (cliente, archivo, copias, tipo, tamano, urgente,
                                 precio, minutos, estado, pago, fecha, fecha_fin_estimada, usuario_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, tuple(datos) + (usuario_id,))
        self.conn.commit()
        return cur.lastrowid

    def guardar_ruta_archivo(self, pedido_id, ruta):
        self.conn.execute("UPDATE pedidos SET ruta_archivo=? WHERE id=?", (ruta, pedido_id))
        self.conn.commit()

    def marcar_por_confirmar(self, pedido_id):
        self.conn.execute("UPDATE pedidos SET estado='Por confirmar', aviso_expirado=1 WHERE id=?", (pedido_id,))
        self.conn.commit()

    def registrar_respuesta_cliente(self, pedido_id, respuesta):
        if respuesta == "Sí, recibido":
            self.conn.execute(
                "UPDATE pedidos SET respuesta_cliente=?, estado='Entregado' WHERE id=?",
                (respuesta, pedido_id)
            )
        else:
            self.conn.execute(
                "UPDATE pedidos SET respuesta_cliente=? WHERE id=?",
                (respuesta, pedido_id)
            )
        self.conn.commit()

    def actualizar(self, pedido_id, estado, pago):
        self.conn.execute("UPDATE pedidos SET estado=?, pago=? WHERE id=?", (estado, pago, pedido_id))
        self.conn.commit()

    def cancelar(self, pedido_id):
        self.conn.execute("UPDATE pedidos SET estado='Cancelado' WHERE id=?", (pedido_id,))
        self.conn.commit()

    def eliminar(self, pedido_id):
        self.conn.execute("DELETE FROM pedidos WHERE id=?", (pedido_id,))
        self.conn.commit()

    def cerrar(self):
        self.conn.close()


class NexoPrintApp:
    def __init__(self, root):
        self.root = root
        self.root.title("NexoPrint | Gestión de pedidos")
        self.root.geometry("1240x780")
        self.root.minsize(1080, 680)
        self.root.configure(bg=COLORS["bg"])
        self.db = BaseDatos(DB_PATH)
        self.seleccion_id = None
        self.admin = False
        self.usuario_actual = None
        self.nav_buttons = {}
        self.configurar_estilos()
        self.crear_estructura()
        self.mostrar_vista("Inicio")
        self.actualizar_todo()
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)
        self.mostrar_login()
        self.root.after(12000, self.revisar_tiempos_entrega)

    def configurar_estilos(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=COLORS["white"], foreground=COLORS["text"],
                        fieldbackground=COLORS["white"], rowheight=34, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#eaf0f8", foreground=COLORS["navy"],
                        font=("Segoe UI", 9, "bold"), padding=9, relief="flat")
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", COLORS["navy"])])
        style.configure("TCombobox", padding=7, font=("Segoe UI", 10))
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)

    def crear_estructura(self):
        self.sidebar = tk.Frame(self.root, bg=COLORS["navy"], width=225)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=COLORS["navy"])
        brand.pack(fill="x", padx=20, pady=(28, 34))
        tk.Label(brand, text="NP", bg=COLORS["blue"], fg="white",
                 font=("Segoe UI", 17, "bold"), width=3, height=1).pack(anchor="w", pady=(0, 12))
        tk.Label(brand, text="NEXOPRINT", bg=COLORS["navy"], fg="white",
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(brand, text="PAPELERÍA Y FOTOCOPIADORA", bg=COLORS["navy"], fg="#a5b4fc",
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(3, 0))

        tk.Label(self.sidebar, text="MENÚ PRINCIPAL", bg=COLORS["navy"], fg="#94a3b8",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=22, pady=(0, 10))
        for name, symbol in [("Inicio", "⌂"), ("Registrar pedido", "+"),
                             ("Pedidos", "▤"), ("Actualizar pedido", "↻")]:
            btn = tk.Button(self.sidebar, text=f"  {symbol}    {name}", anchor="w",
                            command=lambda n=name: self.mostrar_vista(n),
                            bg=COLORS["navy"], fg="#dbeafe", activebackground=COLORS["navy2"],
                            activeforeground="white", relief="flat", bd=0, cursor="hand2",
                            font=("Segoe UI", 10), padx=16, pady=13)
            btn.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[name] = btn

        bottom = tk.Frame(self.sidebar, bg=COLORS["navy"])
        bottom.pack(side="bottom", fill="x", padx=20, pady=22)
        tk.Frame(bottom, bg="#334155", height=1).pack(fill="x", pady=(0, 14))
        tk.Label(bottom, text="Proyecto académico", bg=COLORS["navy"], fg="#cbd5e1",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(bottom, text="Miguel Angel Alvarez Garcia", bg=COLORS["navy"], fg="#94a3b8",
                 font=("Segoe UI", 8), wraplength=180, justify="left").pack(anchor="w", pady=(4, 0))

        self.main = tk.Frame(self.root, bg=COLORS["bg"])
        self.main.pack(side="left", fill="both", expand=True)
        self.topbar = tk.Frame(self.main, bg="white", height=66)
        self.topbar.pack(fill="x")
        self.topbar.pack_propagate(False)
        self.page_title = tk.Label(self.topbar, text="Inicio", bg="white", fg=COLORS["text"],
                                   font=("Segoe UI", 17, "bold"))
        self.page_title.pack(side="left", padx=30)
        self.clock_label = tk.Label(self.topbar, text=datetime.now().strftime("%d/%m/%Y"),
                                    bg="white", fg=COLORS["muted"], font=("Segoe UI", 9))
        self.clock_label.pack(side="right", padx=30)
        self.admin_button = tk.Button(self.topbar, text="🔒 Iniciar sesión administrador",
                                      command=self.cambiar_sesion_admin, bg=COLORS["blue_light"],
                                      fg=COLORS["navy2"], activebackground="#dbeafe",
                                      relief="flat", cursor="hand2", font=("Segoe UI", 9, "bold"), padx=12, pady=7)
        self.admin_button.pack(side="right", padx=10)
        self.content = tk.Frame(self.main, bg=COLORS["bg"])
        self.content.pack(fill="both", expand=True, padx=28, pady=24)

    def limpiar_contenido(self):
        for widget in self.content.winfo_children():
            widget.destroy()

    def mostrar_vista(self, nombre):
        if nombre == "Actualizar pedido" and not self.admin:
            messagebox.showwarning("Acceso restringido", "Solo el administrador puede modificar estados y pagos. Inicia sesión para continuar.")
            return
        self.limpiar_contenido()
        self.page_title.config(text=nombre)
        for key, btn in self.nav_buttons.items():
            btn.config(bg=COLORS["navy2"] if key == nombre else COLORS["navy"],
                       fg="white" if key == nombre else "#dbeafe")
        if nombre == "Inicio":
            self.vista_inicio()
        elif nombre == "Registrar pedido":
            self.vista_registro()
        elif nombre == "Pedidos":
            self.vista_pedidos()
        elif nombre == "Actualizar pedido":
            self.vista_actualizar()

    def tarjeta(self, parent, titulo, valor, color, subtitulo):
        card = tk.Frame(parent, bg="white", highlightbackground=COLORS["line"], highlightthickness=1)
        tk.Frame(card, bg=color, height=4).pack(fill="x")
        tk.Label(card, text=titulo.upper(), bg="white", fg=COLORS["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=17, pady=(15, 3))
        tk.Label(card, text=str(valor), bg="white", fg=COLORS["text"],
                 font=("Segoe UI", 23, "bold")).pack(anchor="w", padx=17)
        tk.Label(card, text=subtitulo, bg="white", fg=COLORS["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w", padx=17, pady=(1, 15))
        return card

    def estadisticas(self):
        pedidos = self.db.todos(None if self.admin else (self.usuario_actual["id"] if self.usuario_actual else -1))
        return {
            "total": len(pedidos),
            "pendientes": sum(p["estado"] == "Pendiente" for p in pedidos),
            "proceso": sum(p["estado"] == "En proceso" for p in pedidos),
            "listos": sum(p["estado"] == "Listo" for p in pedidos),
            "entregados": sum(p["estado"] == "Entregado" for p in pedidos),
            "ingresos": sum(p["precio"] for p in pedidos if p["pago"] == "Pagado"),
        }

    def vista_inicio(self):
        stats = self.estadisticas()
        tk.Label(self.content, text="Resumen general", bg=COLORS["bg"], fg=COLORS["text"],
                 font=("Segoe UI", 19, "bold")).pack(anchor="w")
        tk.Label(self.content, text="Consulta rápida del movimiento de tu papelería.",
                 bg=COLORS["bg"], fg=COLORS["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 18))

        cards = tk.Frame(self.content, bg=COLORS["bg"])
        cards.pack(fill="x")
        card_data = [("Pedidos registrados", stats["total"], COLORS["blue"], "Total histórico"),
                     ("Pendientes", stats["pendientes"], COLORS["orange"], "Esperando atención"),
                     ("En proceso", stats["proceso"], COLORS["purple"], "Actualmente trabajando"),
                     ("Listos", stats["listos"], COLORS["green"], "Esperando entrega"),
                     ("Entregados", stats["entregados"], "#0f766e", "Pedidos finalizados")]
        for i, data in enumerate(card_data):
            card = self.tarjeta(cards, *data)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 9, 0))
            cards.columnconfigure(i, weight=1)

        lower = tk.Frame(self.content, bg=COLORS["bg"])
        lower.pack(fill="both", expand=True, pady=(22, 0))
        recent = tk.Frame(lower, bg="white", highlightbackground=COLORS["line"], highlightthickness=1)
        recent.pack(side="left", fill="both", expand=True, padx=(0, 12))
        tk.Label(recent, text="Pedidos recientes", bg="white", fg=COLORS["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 10))
        cols = ("id", "cliente", "archivo", "precio", "estado")
        tree = ttk.Treeview(recent, columns=cols, show="headings", height=8)
        for c, h, w in [("id", "Pedido", 65), ("cliente", "Cliente", 150), ("archivo", "Archivo", 170),
                        ("precio", "Total", 100), ("estado", "Estado", 110)]:
            tree.heading(c, text=h); tree.column(c, width=w, anchor="center")
        tree.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        pedidos_visibles = self.db.todos(None if self.admin else (self.usuario_actual["id"] if self.usuario_actual else -1))
        for p in pedidos_visibles[:8]:
            tree.insert("", "end", values=(f"#{p['id']}", p["cliente"], p["archivo"],
                                              formato_dinero(p["precio"]), p["estado"]))

        side = tk.Frame(lower, bg="white", width=235, highlightbackground=COLORS["line"], highlightthickness=1)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Resumen de caja", bg="white", fg=COLORS["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=17, pady=(17, 5))
        tk.Label(side, text="Ingresos registrados", bg="white", fg=COLORS["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=17, pady=(12, 2))
        tk.Label(side, text=formato_dinero(stats["ingresos"]), bg="white", fg=COLORS["green"],
                 font=("Segoe UI", 21, "bold")).pack(anchor="w", padx=17)
        tk.Label(side, text="Suma de pedidos marcados como pagados.", bg="white", fg=COLORS["muted"],
                 font=("Segoe UI", 8), wraplength=190, justify="left").pack(anchor="w", padx=17, pady=(5, 18))
        self.boton(side, "＋  Registrar pedido", lambda: self.mostrar_vista("Registrar pedido"), "primary").pack(fill="x", padx=15, pady=5)
        self.boton(side, "▤  Ver todos los pedidos", lambda: self.mostrar_vista("Pedidos"), "light").pack(fill="x", padx=15, pady=5)

    def boton(self, parent, texto, comando, estilo="primary"):
        if estilo == "primary":
            bg, fg, active = COLORS["blue"], "white", COLORS["navy2"]
        elif estilo == "danger":
            bg, fg, active = COLORS["red"], "white", "#991b1b"
        else:
            bg, fg, active = "#eaf0f8", COLORS["navy"], "#dbeafe"
        return tk.Button(parent, text=texto, command=comando, bg=bg, fg=fg,
                         activebackground=active, activeforeground=fg, relief="flat",
                         cursor="hand2", font=("Segoe UI", 9, "bold"), padx=13, pady=9)

    def campo(self, parent, texto, variable, row, col, width=28):
        tk.Label(parent, text=texto, bg="white", fg=COLORS["muted"],
                 font=("Segoe UI", 9, "bold")).grid(row=row, column=col, sticky="w", padx=10, pady=(10, 5))
        entry = tk.Entry(parent, textvariable=variable, width=width, font=("Segoe UI", 10),
                         relief="solid", bd=1, highlightthickness=1, highlightbackground=COLORS["line"],
                         highlightcolor=COLORS["blue"])
        entry.grid(row=row + 1, column=col, sticky="ew", padx=10, ipady=8)
        return entry

    def vista_registro(self):
        tk.Label(self.content, text="Registrar un nuevo pedido", bg=COLORS["bg"], fg=COLORS["text"],
                 font=("Segoe UI", 19, "bold")).pack(anchor="w")
        tk.Label(self.content, text="Completa los datos para calcular automáticamente el valor y el tiempo.",
                 bg=COLORS["bg"], fg=COLORS["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 17))
        panel = tk.Frame(self.content, bg="white", highlightbackground=COLORS["line"], highlightthickness=1)
        panel.pack(fill="x", anchor="n")
        form = tk.Frame(panel, bg="white", padx=20, pady=16)
        form.pack(fill="x")
        self.reg_cliente = tk.StringVar(); self.reg_archivo = tk.StringVar(); self.reg_copias = tk.StringVar()
        self.reg_ruta_archivo = tk.StringVar(value="")
        self.reg_tipo = tk.StringVar(value="B/N"); self.reg_tamano = tk.StringVar(value="Carta")
        self.reg_urgente = tk.BooleanVar(value=False)
        self.campo(form, "NOMBRE DEL CLIENTE", self.reg_cliente, 0, 0)
        self.campo(form, "ARCHIVO SELECCIONADO", self.reg_archivo, 0, 1)
        self.boton(form, "Adjuntar archivo", self.seleccionar_archivo, "light").grid(row=1, column=2, sticky="ew", padx=10, pady=(0, 0))
        self.campo(form, "CANTIDAD DE COPIAS", self.reg_copias, 2, 0)
        tk.Label(form, text="TIPO DE IMPRESIÓN", bg="white", fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).grid(row=2, column=1, sticky="w", padx=10, pady=(10, 5))
        ttk.Combobox(form, textvariable=self.reg_tipo, values=list(PRECIOS.keys()), state="readonly").grid(row=3, column=1, sticky="ew", padx=10, ipady=5)
        tk.Label(form, text="TAMAÑO DEL PAPEL", bg="white", fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).grid(row=2, column=2, sticky="w", padx=10, pady=(10, 5))
        ttk.Combobox(form, textvariable=self.reg_tamano, values=["Carta", "Oficio", "A4"], state="readonly").grid(row=3, column=2, sticky="ew", padx=10, ipady=5)
        for i in range(3): form.columnconfigure(i, weight=1)
        tk.Checkbutton(form, text="Pedido urgente (recargo del 20 % y tiempo reducido)", variable=self.reg_urgente,
                       bg="white", fg=COLORS["orange"], activebackground="white",
                       font=("Segoe UI", 9, "bold")).grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=20)
        actions = tk.Frame(form, bg="white")
        actions.grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 5))
        self.boton(actions, "Guardar pedido", self.registrar_pedido).pack(side="left")
        self.boton(actions, "Limpiar campos", self.limpiar_registro, "light").pack(side="left", padx=10)
        self.resumen_registro = tk.Label(self.content, text="El precio y el tiempo se calcularán al guardar el pedido.",
                                         bg=COLORS["blue_light"], fg=COLORS["navy2"], padx=16, pady=13,
                                         font=("Segoe UI", 9))
        self.resumen_registro.pack(fill="x", pady=15)

    def seleccionar_archivo(self):
        ruta = filedialog.askopenfilename(
            title="Seleccionar archivo para imprimir",
            filetypes=[
                ("Documentos e imágenes", "*.pdf *.doc *.docx *.xls *.xlsx *.ppt *.pptx *.jpg *.jpeg *.png"),
                ("Todos los archivos", "*.*")
            ]
        )
        if ruta:
            self.reg_ruta_archivo.set(ruta)
            self.reg_archivo.set(Path(ruta).name)

    def limpiar_registro(self):
        self.reg_cliente.set(""); self.reg_archivo.set(""); self.reg_copias.set(""); self.reg_ruta_archivo.set("")
        self.reg_tipo.set("B/N"); self.reg_tamano.set("Carta"); self.reg_urgente.set(False)

    def registrar_pedido(self):
        cliente = self.reg_cliente.get().strip()
        archivo = self.reg_archivo.get().strip()
        if not cliente or not archivo or not self.reg_ruta_archivo.get():
            messagebox.showwarning("Datos incompletos", "Ingresa el nombre del cliente y adjunta el archivo que se va a imprimir.")
            return
        try:
            copias = int(self.reg_copias.get())
            if copias <= 0 or copias > 100000:
                raise ValueError
        except ValueError:
            messagebox.showerror("Cantidad no válida", "Ingresa un número entero entre 1 y 100.000.")
            return
        tipo, tamano, urgente = self.reg_tipo.get(), self.reg_tamano.get(), self.reg_urgente.get()
        precio = calcular_precio(copias, tipo, tamano, urgente)
        minutos = calcular_minutos(copias, tipo, urgente)
        ahora = datetime.now()
        fecha = ahora.strftime("%d/%m/%Y %H:%M")
        fecha_fin = (ahora + timedelta(minutes=minutos)).isoformat(timespec="seconds")
        if not self.admin and not self.usuario_actual:
            messagebox.showwarning("Inicia sesión", "Debes iniciar sesión para registrar un pedido.")
            return
        if not self.admin:
            cliente = self.usuario_actual["nombre"]
        pedido_id = self.db.crear((cliente, archivo, copias, tipo, tamano, int(urgente), precio,
                                   minutos, "Pendiente", "Pendiente", fecha, fecha_fin),
                                  None if self.admin else self.usuario_actual["id"])
        try:
            carpeta_archivos = APP_DIR / "archivos_pedidos"
            carpeta_archivos.mkdir(parents=True, exist_ok=True)
            nombre_seguro = Path(archivo).name
            destino = carpeta_archivos / f"pedido_{pedido_id}_{nombre_seguro}"
            shutil.copy2(self.reg_ruta_archivo.get(), destino)
            self.db.guardar_ruta_archivo(pedido_id, str(destino))
        except OSError as error:
            messagebox.showwarning(
                "Archivo no copiado",
                f"El pedido se registró, pero no se pudo guardar una copia del archivo.\\n{error}"
            )
        self.actualizar_todo()
        self.resumen_registro.config(text=f"Pedido #{pedido_id} guardado  •  Total: {formato_dinero(precio)}  •  Tiempo estimado: {minutos} minutos")
        self.limpiar_registro()
        messagebox.showinfo("Pedido registrado", f"El pedido #{pedido_id} se guardó correctamente.\n\nValor: {formato_dinero(precio)}\nTiempo estimado: {minutos} minutos")

    def vista_pedidos(self):
        tk.Label(self.content, text="Listado de pedidos", bg=COLORS["bg"], fg=COLORS["text"],
                 font=("Segoe UI", 19, "bold")).pack(anchor="w")
        tk.Label(self.content, text="Busca por cliente, archivo o número de pedido. También puedes filtrar por estado.",
                 bg=COLORS["bg"], fg=COLORS["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 15))
        filters = tk.Frame(self.content, bg=COLORS["bg"]); filters.pack(fill="x", pady=(0, 12))
        self.busqueda = tk.StringVar(); self.filtro_estado = tk.StringVar(value="Todos")
        search = tk.Entry(filters, textvariable=self.busqueda, font=("Segoe UI", 10), relief="solid", bd=1)
        search.pack(side="left", fill="x", expand=True, ipady=8)
        search.insert(0, "")
        ttk.Combobox(filters, textvariable=self.filtro_estado, values=["Todos"] + ESTADOS,
                     state="readonly", width=18).pack(side="left", padx=10, ipady=5)
        self.boton(filters, "Exportar CSV", self.exportar_csv, "light").pack(side="right")
        self.busqueda.trace_add("write", lambda *_: self.cargar_tabla())
        self.filtro_estado.trace_add("write", lambda *_: self.cargar_tabla())
        tablebox = tk.Frame(self.content, bg="white", highlightbackground=COLORS["line"], highlightthickness=1)
        tablebox.pack(fill="both", expand=True)
        columns = ("id", "cliente", "archivo", "copias", "tipo", "tamano", "urgente", "precio", "tiempo", "estado", "pago", "respuesta")
        self.tree = ttk.Treeview(tablebox, columns=columns, show="headings")
        heads = {"id":"N.º", "cliente":"Cliente", "archivo":"Archivo", "copias":"Copias", "tipo":"Tipo",
                 "tamano":"Papel", "urgente":"Urgente", "precio":"Precio", "tiempo":"Tiempo",
                 "estado":"Estado", "pago":"Pago", "respuesta":"Confirmación"}
        widths = {"id":48, "cliente":125, "archivo":145, "copias":60, "tipo":65, "tamano":70,
                  "urgente":65, "precio":95, "tiempo":75, "estado":105, "pago":85, "respuesta":115}
        for c in columns:
            self.tree.heading(c, text=heads[c]); self.tree.column(c, width=widths[c], anchor="center")
        sy = ttk.Scrollbar(tablebox, orient="vertical", command=self.tree.yview)
        sx = ttk.Scrollbar(tablebox, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.pack(side="top", fill="both", expand=True)
        sx.pack(side="bottom", fill="x"); sy.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self.cargar_seleccion())
        bottom = tk.Frame(self.content, bg=COLORS["bg"]); bottom.pack(fill="x", pady=(10, 0))
        self.boton(bottom, "Ver / actualizar seleccionado", self.abrir_seleccion, "primary").pack(side="left")
        self.boton(bottom, "Abrir archivo adjunto", self.abrir_archivo_seleccionado, "light").pack(side="left", padx=9)
        self.boton(bottom, "Confirmar recepción", self.confirmar_recepcion_seleccionada, "light").pack(side="left", padx=9)
        if not self.admin:
            self.boton(bottom, "Cancelar mi pedido", self.cancelar_pedido_cliente, "danger").pack(side="left", padx=9)
        if self.admin:
            self.boton(bottom, "Eliminar seleccionado", self.eliminar_seleccionado, "danger").pack(side="left", padx=2)
        self.cargar_tabla()

    def cargar_tabla(self):
        if not hasattr(self, "tree") or not self.tree.winfo_exists(): return
        for item in self.tree.get_children(): self.tree.delete(item)
        texto = self.busqueda.get().strip().lower()
        estado = self.filtro_estado.get()
        pedidos_visibles = self.db.todos(None if self.admin else (self.usuario_actual["id"] if self.usuario_actual else -1))
        for p in pedidos_visibles:
            if estado != "Todos" and p["estado"] != estado: continue
            if texto and texto not in str(p["id"]).lower() and texto not in p["cliente"].lower() and texto not in p["archivo"].lower(): continue
            self.tree.insert("", "end", iid=str(p["id"]), values=(p["id"], p["cliente"], p["archivo"], p["copias"],
                p["tipo"], p["tamano"], "Sí" if p["urgente"] else "No", formato_dinero(p["precio"]),
                f"{p['minutos']} min", p["estado"], p["pago"], p["respuesta_cliente"]))

    def cargar_seleccion(self):
        if not hasattr(self, "tree"): return None
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selecciona un pedido", "Primero selecciona un pedido de la tabla.")
            return None
        return int(selected[0])

    def abrir_archivo_seleccionado(self):
        pedido_id = self.cargar_seleccion()
        if pedido_id is None:
            return
        pedido = self.db.obtener(pedido_id)
        self.abrir_archivo_pedido(pedido)

    def abrir_archivo_pedido(self, pedido):
        if not pedido or not pedido["ruta_archivo"]:
            messagebox.showinfo("Sin archivo adjunto", "Este pedido no tiene un archivo guardado.")
            return
        ruta = Path(pedido["ruta_archivo"])
        if not ruta.exists():
            messagebox.showerror("Archivo no encontrado", f"No se encontró el archivo guardado:\\n{ruta}")
            return
        try:
            os.startfile(str(ruta))
        except (AttributeError, OSError):
            messagebox.showerror("No se pudo abrir", "No fue posible abrir el archivo en este equipo.")

    def abrir_seleccion(self):
        pedido_id = self.cargar_seleccion()
        if pedido_id is not None:
            self.mostrar_vista("Actualizar pedido")
            self.id_actualizar.set(str(pedido_id))
            self.buscar_para_actualizar()

    def eliminar_seleccionado(self):
        if not self.admin:
            messagebox.showwarning("Acceso restringido", "Solo el administrador puede eliminar pedidos."); return
        pedido_id = self.cargar_seleccion()
        if pedido_id is None: return
        if messagebox.askyesno("Confirmar eliminación", f"¿Eliminar definitivamente el pedido #{pedido_id}? Esta acción no se puede deshacer."):
            self.db.eliminar(pedido_id); self.cargar_tabla(); self.actualizar_todo()

    def exportar_csv(self):
        pedidos = self.db.todos(None if self.admin else (self.usuario_actual["id"] if self.usuario_actual else -1))
        if not pedidos:
            messagebox.showinfo("Sin datos", "No hay pedidos para exportar."); return
        ruta = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("Archivo CSV", "*.csv")],
                                            initialfile="pedidos_nexoprint.csv")
        if not ruta: return
        with open(ruta, "w", newline="", encoding="utf-8-sig") as archivo:
            writer = csv.writer(archivo, delimiter=";")
            writer.writerow(["Número", "Cliente", "Archivo", "Copias", "Tipo", "Tamaño", "Urgente", "Precio", "Minutos", "Estado", "Pago", "Fecha", "Entrega estimada", "Respuesta del cliente"])
            for p in pedidos:
                writer.writerow([p["id"], p["cliente"], p["archivo"], p["copias"], p["tipo"], p["tamano"],
                                 "Sí" if p["urgente"] else "No", p["precio"], p["minutos"], p["estado"], p["pago"], p["fecha"],
                                 p["fecha_fin_estimada"] or "", p["respuesta_cliente"]])
        messagebox.showinfo("Exportación completada", f"Se exportaron {len(pedidos)} pedidos correctamente.")

    def vista_actualizar(self):
        tk.Label(self.content, text="Actualizar pedido", bg=COLORS["bg"], fg=COLORS["text"],
                 font=("Segoe UI", 19, "bold")).pack(anchor="w")
        tk.Label(self.content, text="Solo disponible para el administrador. El cliente no puede modificar estados ni pagos.",
                 bg=COLORS["bg"], fg=COLORS["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 16))
        panel = tk.Frame(self.content, bg="white", padx=22, pady=22,
                         highlightbackground=COLORS["line"], highlightthickness=1)
        panel.pack(fill="x", anchor="n")
        self.id_actualizar = tk.StringVar(); self.estado_actualizar = tk.StringVar(value="Pendiente")
        self.pago_actualizar = tk.StringVar(value="Pendiente")
        row = tk.Frame(panel, bg="white"); row.pack(fill="x")
        tk.Label(row, text="NÚMERO DE PEDIDO", bg="white", fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=8)
        tk.Entry(row, textvariable=self.id_actualizar, font=("Segoe UI", 10), relief="solid", bd=1, width=18).grid(row=1, column=0, sticky="ew", padx=8, ipady=8)
        self.boton(row, "Buscar pedido", self.buscar_para_actualizar, "light").grid(row=1, column=1, padx=8)
        tk.Label(row, text="NUEVO ESTADO", bg="white", fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="w", padx=8)
        ttk.Combobox(row, textvariable=self.estado_actualizar, values=ESTADOS, state="readonly", width=18).grid(row=1, column=2, padx=8, ipady=5)
        tk.Label(row, text="ESTADO DEL PAGO", bg="white", fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).grid(row=0, column=3, sticky="w", padx=8)
        ttk.Combobox(row, textvariable=self.pago_actualizar, values=PAGOS, state="readonly", width=16).grid(row=1, column=3, padx=8, ipady=5)
        for i in range(4): row.columnconfigure(i, weight=1)
        self.detalle_actualizar = tk.Label(panel, text="Busca un pedido para ver sus detalles.", bg=COLORS["blue_light"],
                                           fg=COLORS["navy2"], justify="left", anchor="w", padx=15, pady=15,
                                           font=("Segoe UI", 10))
        self.detalle_actualizar.pack(fill="x", pady=20)
        actions = tk.Frame(panel, bg="white"); actions.pack(fill="x")
        self.boton(actions, "Guardar cambios", self.guardar_actualizacion).pack(side="left")
        self.boton(actions, "Cancelar pedido", self.cancelar_desde_actualizar, "danger").pack(side="left", padx=9)
        self.boton(actions, "Abrir archivo adjunto", self.abrir_archivo_desde_actualizar, "light").pack(side="left", padx=9)

    def abrir_archivo_desde_actualizar(self):
        try:
            pedido_id = int(self.id_actualizar.get())
        except (ValueError, AttributeError):
            messagebox.showwarning("Pedido no seleccionado", "Busca primero un pedido.")
            return
        self.abrir_archivo_pedido(self.db.obtener(pedido_id))

    def buscar_para_actualizar(self):
        try:
            pedido_id = int(self.id_actualizar.get())
        except (ValueError, AttributeError):
            messagebox.showerror("Número no válido", "Ingresa un número de pedido entero."); return
        p = self.db.obtener(pedido_id)
        if not p:
            self.detalle_actualizar.config(text="No se encontró un pedido con ese número.")
            messagebox.showinfo("No encontrado", "No existe un pedido con ese número."); return
        self.estado_actualizar.set(p["estado"]); self.pago_actualizar.set(p["pago"])
        self.detalle_actualizar.config(text=(f"PEDIDO #{p['id']}\nCliente: {p['cliente']}\nArchivo: {p['archivo']}\n"
            f"Copias: {p['copias']}  |  Impresión: {p['tipo']}  |  Papel: {p['tamano']}\n"
            f"Urgente: {'Sí' if p['urgente'] else 'No'}  |  Total: {formato_dinero(p['precio'])}\n"
            f"Tiempo estimado: {p['minutos']} minutos  |  Fecha: {p['fecha']}\nEntrega estimada: {p['fecha_fin_estimada'] or 'No disponible'}\nEstado actual: {p['estado']}  |  Pago: {p['pago']}\nConfirmación del cliente: {p['respuesta_cliente']}"))

    def guardar_actualizacion(self):
        if not self.admin:
            messagebox.showwarning("Acceso restringido", "Solo el administrador puede cambiar el estado o el pago."); return
        try: pedido_id = int(self.id_actualizar.get())
        except ValueError:
            messagebox.showerror("Dato no válido", "Busca primero un pedido válido."); return
        if not self.db.obtener(pedido_id):
            messagebox.showerror("No encontrado", "No existe ese pedido."); return
        self.db.actualizar(pedido_id, self.estado_actualizar.get(), self.pago_actualizar.get())
        self.buscar_para_actualizar(); self.actualizar_todo()
        messagebox.showinfo("Actualización", f"El pedido #{pedido_id} se actualizó correctamente.")

    def cancelar_desde_actualizar(self):
        if not self.admin:
            messagebox.showwarning("Acceso restringido", "Solo el administrador puede cancelar pedidos."); return
        try: pedido_id = int(self.id_actualizar.get())
        except ValueError:
            messagebox.showerror("Dato no válido", "Ingresa el número del pedido."); return
        p = self.db.obtener(pedido_id)
        if not p:
            messagebox.showerror("No encontrado", "No existe ese pedido."); return
        if p["estado"] == "Cancelado":
            messagebox.showinfo("Pedido", "Este pedido ya está cancelado."); return
        if messagebox.askyesno("Confirmar", f"¿Deseas cancelar el pedido #{pedido_id}?"):
            self.db.cancelar(pedido_id); self.buscar_para_actualizar(); self.actualizar_todo()
            messagebox.showinfo("Cancelado", f"El pedido #{pedido_id} fue cancelado.")

    def actualizar_boton_sesion(self):
        if self.admin:
            self.admin_button.config(text="🔓 Administrador · Cerrar sesión")
        elif self.usuario_actual:
            self.admin_button.config(text=f"👤 {self.usuario_actual['nombre']} · Cerrar sesión")
        else:
            self.admin_button.config(text="🔒 Iniciar sesión")

    def mostrar_login(self):
        ventana = tk.Toplevel(self.root)
        ventana.title("Iniciar sesión | NexoPrint")
        ventana.configure(bg="white")
        ventana.resizable(False, False)
        ventana.transient(self.root)
        ventana.grab_set()
        marco = tk.Frame(ventana, bg="white", padx=28, pady=24)
        marco.pack(fill="both", expand=True)
        tk.Label(marco, text="Bienvenido a NexoPrint", bg="white", fg=COLORS["text"],
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(marco, text="Inicia sesión para consultar y gestionar tus pedidos.",
                 bg="white", fg=COLORS["muted"], wraplength=330, justify="left").pack(anchor="w", pady=(5, 18))

        tk.Label(marco, text="Correo electrónico", bg="white", fg=COLORS["muted"]).pack(anchor="w")
        correo = tk.Entry(marco, font=("Segoe UI", 10), width=38)
        correo.pack(fill="x", pady=(3, 10), ipady=7)
        tk.Label(marco, text="Contraseña", bg="white", fg=COLORS["muted"]).pack(anchor="w")
        clave = tk.Entry(marco, show="•", font=("Segoe UI", 10))
        clave.pack(fill="x", pady=(3, 14), ipady=7)

        def entrar(event=None):
            email = correo.get().strip()
            password = clave.get()
            if email.lower() == USUARIO_ADMIN and password == CLAVE_ADMIN:
                self.admin = True
                self.usuario_actual = None
                ventana.destroy()
                self.root.deiconify()
                self.actualizar_boton_sesion()
                self.mostrar_vista("Inicio")
                messagebox.showinfo("Sesión iniciada", "Bienvenido, administrador.")
                return
            usuario = self.db.autenticar_usuario(email, password)
            if usuario:
                self.admin = False
                self.usuario_actual = usuario
                ventana.destroy()
                self.root.deiconify()
                self.actualizar_boton_sesion()
                self.mostrar_vista("Inicio")
                messagebox.showinfo("Sesión iniciada", f"Bienvenido, {usuario['nombre']}.")
            else:
                messagebox.showerror("Acceso denegado", "Correo o contraseña incorrectos.")
                clave.delete(0, "end")

        def registro():
            ventana.destroy()
            self.mostrar_registro_usuario()

        self.boton(marco, "Iniciar sesión", entrar).pack(fill="x")
        self.boton(marco, "Crear cuenta de cliente", registro, "light").pack(fill="x", pady=(8, 0))
        tk.Label(marco, text="Administrador: usuario admin | contraseña Nexoprint",
                 bg="white", fg=COLORS["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(14, 0))
        correo.focus_set()
        clave.bind("<Return>", entrar)
        ventana.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)

    def mostrar_registro_usuario(self):
        ventana = tk.Toplevel(self.root)
        ventana.title("Crear cuenta | NexoPrint")
        ventana.configure(bg="white")
        ventana.resizable(False, False)
        ventana.transient(self.root)
        ventana.grab_set()
        marco = tk.Frame(ventana, bg="white", padx=28, pady=24)
        marco.pack(fill="both", expand=True)
        tk.Label(marco, text="Crear cuenta de cliente", bg="white", fg=COLORS["text"],
                 font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 15))

        campos = {}
        for etiqueta, clave_campo, ocultar in [
            ("Nombre completo", "nombre", False),
            ("Correo electrónico", "correo", False),
            ("Contraseña (mínimo 6 caracteres)", "clave", True),
            ("Confirmar contraseña", "confirmar", True)
        ]:
            tk.Label(marco, text=etiqueta, bg="white", fg=COLORS["muted"]).pack(anchor="w")
            entrada = tk.Entry(marco, font=("Segoe UI", 10), show="•" if ocultar else "")
            entrada.pack(fill="x", pady=(3, 10), ipady=7)
            campos[clave_campo] = entrada

        def crear():
            nombre = campos["nombre"].get().strip()
            email = campos["correo"].get().strip().lower()
            password = campos["clave"].get()
            confirmar = campos["confirmar"].get()
            if not nombre or not email or not password or not confirmar:
                messagebox.showwarning("Datos incompletos", "Completa todos los campos.", parent=ventana)
                return
            if "@" not in email or "." not in email.split("@")[-1]:
                messagebox.showwarning("Correo no válido", "Escribe un correo electrónico válido.", parent=ventana)
                return
            if len(password) < 6:
                messagebox.showwarning("Contraseña débil", "La contraseña debe tener al menos 6 caracteres.", parent=ventana)
                return
            if password != confirmar:
                messagebox.showwarning("No coincide", "Las contraseñas no coinciden.", parent=ventana)
                return
            user_id = self.db.registrar_usuario(nombre, email, password)
            if user_id is None:
                messagebox.showerror("Correo registrado", "Ya existe una cuenta con ese correo.", parent=ventana)
                return
            ventana.destroy()
            self.mostrar_login()
            messagebox.showinfo("Cuenta creada", "Tu cuenta se creó correctamente. Ahora puedes iniciar sesión.")

        self.boton(marco, "Crear cuenta", crear).pack(fill="x")
        self.boton(marco, "Volver al inicio de sesión",
                   lambda: (ventana.destroy(), self.mostrar_login()), "light").pack(fill="x", pady=(8, 0))
        ventana.protocol("WM_DELETE_WINDOW", lambda: (ventana.destroy(), self.mostrar_login()))

    def cambiar_sesion_admin(self):
        # Este botón ahora permite cerrar la sesión actual y volver al acceso común.
        if self.admin or self.usuario_actual:
            self.admin = False
            self.usuario_actual = None
            self.actualizar_boton_sesion()
            self.mostrar_login()
            return
        self.mostrar_login()

    def confirmar_recepcion_seleccionada(self):
        pedido_id = self.cargar_seleccion()
        if pedido_id is None: return
        p = self.db.obtener(pedido_id)
        if not p or (not self.admin and p["usuario_id"] != self.usuario_actual["id"]):
            messagebox.showerror("Acceso restringido", "Solo puedes confirmar tus propios pedidos.")
            return
        if not p or p["estado"] not in ("Por confirmar", "Listo"):
            messagebox.showinfo("Confirmación no disponible", "Este pedido todavía no está pendiente de confirmación de entrega.")
            return
        self.preguntar_al_cliente(pedido_id, mostrar_recordatorio=False)

    def cancelar_pedido_cliente(self):
        if self.admin or not self.usuario_actual:
            messagebox.showwarning("Acceso no válido", "Esta opción es para cuentas de cliente.")
            return
        pedido_id = self.cargar_seleccion()
        if pedido_id is None:
            return
        pedido = self.db.obtener(pedido_id)
        if not pedido or pedido["usuario_id"] != self.usuario_actual["id"]:
            messagebox.showerror("Acceso restringido", "Solo puedes cancelar tus propios pedidos.")
            return
        if pedido["estado"] not in ("Pendiente", "En proceso"):
            messagebox.showinfo(
                "No se puede cancelar",
                "Solo puedes cancelar pedidos que estén Pendientes o En proceso."
            )
            return
        if messagebox.askyesno("Cancelar pedido", f"¿Seguro que deseas cancelar tu pedido #{pedido_id}?"):
            if self.db.cancelar_cliente(pedido_id, self.usuario_actual["id"]):
                self.actualizar_todo()
                messagebox.showinfo("Pedido cancelado", f"Tu pedido #{pedido_id} fue cancelado.")
            else:
                messagebox.showwarning("No se pudo cancelar", "El pedido cambió de estado o no te pertenece.")

    def preguntar_al_cliente(self, pedido_id, mostrar_recordatorio=True):
        p = self.db.obtener(pedido_id)
        if not p: return
        recibido = messagebox.askyesno("Confirmación de entrega",
            f"Hola, {p['cliente']}.\n\n¿Recibiste correctamente tu pedido #{pedido_id} ({p['archivo']})?\n\nSí: registrar que lo recibiste.\nNo: avisar al administrador que aún no lo recibiste.")
        respuesta = "Sí, recibido" if recibido else "No, no recibido"
        self.db.registrar_respuesta_cliente(pedido_id, respuesta)
        self.actualizar_todo()
        if recibido:
            messagebox.showinfo(
                "Pedido entregado",
                f"Tu confirmación fue registrada y el pedido #{pedido_id} cambió automáticamente a «Entregado»."
            )
        else:
            messagebox.showinfo(
                "Respuesta registrada",
                "Se registró que todavía no has recibido el pedido. El administrador podrá revisar el caso."
            )

    def revisar_tiempos_entrega(self):
        """Al vencer el tiempo, marca el pedido para confirmar; nunca lo da por entregado sin respuesta."""
        ahora = datetime.now()
        vencidos = []
        for p in self.db.todos():
            if p["estado"] in ("Entregado", "Cancelado", "Por confirmar") or p["aviso_expirado"]:
                continue
            fin = p["fecha_fin_estimada"]
            if not fin: continue
            try: fecha_fin = datetime.fromisoformat(fin)
            except (TypeError, ValueError): continue
            if ahora >= fecha_fin:
                self.db.marcar_por_confirmar(p["id"])
                vencidos.append(p["id"])
        if vencidos:
            self.actualizar_todo()
            # La confirmación se realiza desde la sesión del cliente, en su lista de pedidos.
            if self.admin:
                for pedido_id in vencidos:
                    self.preguntar_al_cliente(pedido_id)
        self.root.after(12000, self.revisar_tiempos_entrega)

    def actualizar_todo(self):
        # Refresca la vista actual y los indicadores al regresar al inicio.
        if hasattr(self, "tree") and self.tree.winfo_exists():
            self.cargar_tabla()

    def cerrar_aplicacion(self):
        self.db.cerrar()
        self.root.destroy()


def main():
    root = tk.Tk()
    NexoPrintApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
