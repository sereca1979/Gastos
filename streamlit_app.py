import streamlit as st
from difflib import SequenceMatcher
from datetime import timedelta
import pandas as pd
import altair as alt
import re
import unicodedata
from datetime import date
from decimal import Decimal
from supabase import create_client
from importadores import ibercaja, trade_republic, huellas

st.set_page_config(page_title='Control de gastos', page_icon='📊', layout='wide')

# Streamlit representa ambas versiones, pero CSS muestra solo la adecuada.
st.markdown('''
<style>
@media (max-width: 700px) {
  .st-key-graficas_escritorio, [class*="st-key-quesito_escritorio_"] {display:none !important;}
  .st-key-graficas_movil, [class*="st-key-quesito_movil_"] {display:block !important;}
  [data-testid="stMainBlockContainer"] {padding-left: .65rem; padding-right: .65rem; padding-top: 1rem;}
  [data-testid="stMainBlockContainer"] h1 {font-size:1.65rem;}
  [data-testid="stMainBlockContainer"] h2,
  [data-testid="stMainBlockContainer"] h3 {font-size:1.22rem;}
  [data-testid="stHorizontalBlock"] {flex-wrap:wrap; gap:.35rem;}
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width: 0 !important; flex: 1 1 30% !important;}
  .indicador-valor {font-size:clamp(1rem,4.4vw,1.5rem) !important; white-space:nowrap;}
  .indicador-titulo {font-size:.78rem !important;}
  [data-testid="stTabs"] [role="tablist"] {overflow-x:auto; flex-wrap:nowrap; scrollbar-width:thin;}
  [data-testid="stTabs"] button[role="tab"] {white-space:nowrap; flex-shrink:0; padding:.55rem .65rem;}
  [data-testid="stDataFrame"], [data-testid="stDataEditor"] {max-width:100%; overflow-x:auto;}
  [data-testid="stFileUploader"] {max-width:100%;}
  [data-testid="stSidebar"] {max-width:85vw;}
  .stButton button, [data-testid="stFormSubmitButton"] button {min-height:42px;}
  .st-key-graficas_movil [data-testid="stVegaLiteChart"] {width:100%;}

}
@media (min-width: 701px) {
  .st-key-graficas_movil, [class*="st-key-quesito_movil_"] {display:none !important;}
}
</style>
''', unsafe_allow_html=True)


def nuevo_cliente():
    # Nunca cachear un cliente que contenga una sesión de usuario.
    return create_client(st.secrets['SUPABASE_URL'], st.secrets['SUPABASE_KEY'])


def cerrar_sesion():
    cliente = st.session_state.get('cliente')
    if cliente:
        try:
            cliente.auth.sign_out()
        except Exception:
            pass
    for clave in ('cliente', 'usuario_id', 'access_token', 'refresh_token'):
        st.session_state.pop(clave, None)
    st.rerun()


def sesion_valida():
    cliente = st.session_state.get('cliente')
    if cliente is None:
        return False
    try:
        sesion = cliente.auth.get_session()
        if not sesion or not sesion.user or sesion.user.id != st.session_state.get('usuario_id'):
            return False
        # Verificar token con el servidor de autenticación.
        verificacion = cliente.auth.get_user()
        return bool(verificacion.user and verificacion.user.id == st.session_state['usuario_id'])
    except Exception:
        return False


def consultar(tabla, columnas='*', orden=None):
    q = st.session_state.cliente.table(tabla).select(columnas)
    if orden:
        q = q.order(orden, desc=True)
    if tabla == 'movimientos':
        todos = []
        for inicio in range(0, 100000, 500):
            lote = q.range(inicio, inicio + 499).execute().data or []
            todos.extend(lote)
            if len(lote) < 500:
                break
        return todos
    return q.execute().data or []


def ejecutar(tabla, accion, payload=None, identificador=None):
    q = st.session_state.cliente.table(tabla)
    if accion == 'insert':
        return q.insert(payload).execute()
    if accion == 'update':
        return q.update(payload).eq('id', identificador).eq('usuario_id', st.session_state.usuario_id).execute()
    if accion == 'delete':
        return q.delete().eq('id', identificador).eq('usuario_id', st.session_state.usuario_id).execute()
    raise ValueError('Acción no admitida')


def dinero(valor):
    return f'{float(valor):,.2f} €'.replace(',', 'X').replace('.', ',').replace('X', '.')


CATEGORIAS_INICIALES = {
    'gasto': ['Alimentación', 'Restaurantes y ocio', 'Vivienda', 'Suministros',
              'Coche y Transportes', 'Salud', 'Ropa y compras',
              'Seguros e impuestos', 'Regalos y donaciones', 'Otros gastos'],
    'ingreso': ['Nómina', 'Intereses', 'Devoluciones', 'Otros ingresos', 'Dani', 'Restaurantes y ocio', 'Regalos y donaciones']
}


def clave_aprendizaje(concepto):
    """Coincidencia conservadora por concepto normalizado, sin datos personales externos."""
    texto = unicodedata.normalize('NFKD', str(concepto).upper())
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', texto).strip()[:400]


def sugerir_categoria(concepto, tipo, aviso=''):
    """Reglas conservadoras: lo ambiguo requiere revisión."""
    import unicodedata
    texto = unicodedata.normalize('NFKD', str(concepto).upper())
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    if tipo == 'transferencia':
        return '', 'Excluido / revisar'
    if tipo == 'ingreso':
        reglas = [
            ('Intereses', ['INTERES', 'INTEREST']),
            ('Nómina', ['NOMINA', 'PAYROLL', 'SALARIO']),
            ('Devoluciones', ['DEVOLUCION', 'REEMBOLSO', 'REFUND', 'REINTEGRO COMPRA']),
        ]
    else:
        reglas = [
            ('Alimentación', ['MERCADONA', 'LIDL', 'ALDI', 'CARREFOUR', 'DIA ', 'EROSKI', 'ALCAMPO', 'SUPERMERCADO']),
            ('Restaurantes y ocio', ['RESTAURANTE', 'CAFETERIA', 'BAR ', 'BURGER KING', 'MCDONALD', 'JUST EAT', 'GLOVO', 'CINE']),
            ('Vivienda', ['ALQUILER', 'COMUNIDAD PROPIETARIOS', 'HIPOTECA']),
            ('Suministros', ['IBERDROLA', 'ENDESA', 'NATURGY', 'MOVISTAR', 'VODAFONE', 'ORANGE', 'DIGI ', 'AGUA ', 'ELECTRICIDAD']),
            ('Coche y Transportes', ['REPSOL', 'CEPSA', 'MOEVE', 'GASOLINERA', 'RENFE', 'CABIFY', 'UBER', 'PARKING', 'METRO ', 'BOOKING', 'AIRBNB', 'HOTEL ', 'IBERIA', 'RYANAIR', 'VUELING']),
            ('Salud', ['FARMACIA', 'DENTISTA', 'CLINICA', 'HOSPITAL', 'OPTICA']),
            ('Ropa y compras', ['ZARA', 'PRIMARK', 'DECATHLON', 'H&M', 'CORTE INGLES']),
            ('Seguros e impuestos', ['SEGURO', 'HACIENDA', 'AEAT', 'IBI ', 'IMPUESTO', 'DGT ']),
            ('Regalos y donaciones', ['DONATIVO', 'DONACION', 'ONG ']),
        ]
    for categoria, patrones in reglas:
        if any(p in texto for p in patrones):
            return categoria, 'Alta'
    if 'BIZUM' in texto:
        return ('Otros ingresos' if tipo == 'ingreso' else 'Otros gastos'), 'Revisar Bizum'
    return ('Otros ingresos' if tipo == 'ingreso' else 'Otros gastos'), 'Revisar'


st.title('Control de gastos')
if not sesion_valida():
    for clave in ('cliente', 'usuario_id'):
        st.session_state.pop(clave, None)
    st.subheader('Iniciar sesión')
    with st.form('login'):
        correo = st.text_input('Correo electrónico')
        contrasena = st.text_input('Contraseña', type='password')
        enviar = st.form_submit_button('Entrar')
    if enviar:
        try:
            cliente = nuevo_cliente()
            respuesta = cliente.auth.sign_in_with_password({'email': correo.strip(), 'password': contrasena})
            if not respuesta.user or not respuesta.session:
                raise ValueError('No se ha recibido una sesión válida')
            st.session_state.cliente = cliente
            st.session_state.usuario_id = respuesta.user.id
            st.rerun()
        except Exception:
            st.error('No se ha podido iniciar sesión. Comprueba el correo, la contraseña y la configuración de Supabase.')
    st.stop()

with st.sidebar:
    st.success('Sesión iniciada')
    if st.button('Cerrar sesión'):
        cerrar_sesion()

try:
    categorias = consultar('categorias', orden='nombre')
    movimientos = consultar('movimientos', orden='fecha')
    # Corte de visualización: no borra registros anteriores en Supabase.
    FECHA_INICIO = '2026-09-01'
    movimientos = [m for m in movimientos if str(m.get('fecha', ''))[:10] >= FECHA_INICIO]
except Exception:
    st.error('No se pueden consultar los datos. Comprueba la conexión y las políticas RLS de Supabase.')
    st.stop()

nombres_categorias = {c['id']: c['nombre'] for c in categorias}
CATEGORIAS_COMPENSACION = {'Restaurantes y ocio', 'Regalos y donaciones'}

def es_excluido(m):
    return bool(m.get('excluir_balance', False))

def categoria_movimiento(m):
    return nombres_categorias.get(m.get('categoria_id'), 'Sin categoría')

def importes_balance(registros):
    ingresos = Decimal('0')
    gastos = Decimal('0')
    for m in registros:
        if es_excluido(m) or m['tipo'] not in ('gasto', 'ingreso'):
            continue
        valor = Decimal(str(m['importe']))
        if m['tipo'] == 'ingreso' and categoria_movimiento(m) in CATEGORIAS_COMPENSACION:
            gastos -= valor
        elif m['tipo'] == 'ingreso':
            ingresos += valor
        else:
            gastos += valor
    return ingresos, gastos


st.info('Histórico visible desde el 1 de septiembre de 2026. Los datos anteriores siguen conservados en Supabase.')
# Paleta estable y amplia: ningún color depende del orden ni del importe.
colores_categorias = {
    'Alimentación': '#277DA1',
    'Restaurantes y ocio': '#F8961E',
    'Vivienda': '#43AA8B',
    'Suministros': '#F9C74F',
    'Coche y Transportes': '#577590',
    'Salud': '#F94144',
    'Ropa y compras': '#9B5DE5',
    'Seguros e impuestos': '#4D908E',
    'Regalos y donaciones': '#F15BB5',
    'Otros gastos': '#90BE6D',
    'Sin categoría': '#8D99AE',
    'Compras': '#00BBF9',
    'Colegio': '#B8A1E3',
    'Dani': '#E76F51',
    'Loterías': '#B56576',
    'Transporte': '#4361EE',
    'Viajes': '#F9844A',
    'Devoluciones': '#2A9D8F',
    'Intereses': '#C77DFF',
    'Otros ingresos': '#06D6A0',
    'Nómina': '#118AB2',
}
import hashlib
colores_extra = ['#E09F3E', '#7B2CBF', '#2EC4B6', '#FF6B6B',
                 '#A1C181', '#B5838D', '#3A86FF', '#E29578',
                 '#7EBDC2', '#CDB4DB', '#DDA15E', '#A8DADC']
def color_de(nombre):
    if nombre in colores_categorias:
        return colores_categorias[nombre]
    idx = int(hashlib.sha256(nombre.encode('utf-8')).hexdigest()[:8], 16)
    return colores_extra[idx % len(colores_extra)]


tabs = st.tabs(['Resumen', 'Movimientos', 'Presupuestos', 'Configuración', 'Importar extractos'])

def indicador_financiero(titulo, valor, tipo):
    """Indicador con etiqueta discreta y cifra destacada por signo económico."""
    valor = Decimal(str(valor))
    # Gastos son salidas de dinero aunque se almacenen como importes positivos.
    favorable = (valor >= 0) if tipo != 'gastos' else False
    color = '#35bd86' if favorable else '#ef7373'
    return f"""<div class=\"indicador-financiero\">
        <div class=\"indicador-titulo\">{titulo}</div>
        <div class=\"indicador-valor\" style=\"color:{color}\">{dinero(valor)}</div>
    </div>"""

st.markdown("""<style>
.indicador-financiero {padding: .25rem 0 .7rem 0;}
.indicador-titulo {font-size: .92rem; font-weight: 500; color: #a7a9af; margin-bottom: .4rem;}
.indicador-valor {font-size: clamp(1.55rem, 2.3vw, 2.15rem); font-weight: 650; line-height: 1.2;}
</style>""", unsafe_allow_html=True)

with tabs[0]:
    st.subheader('Resumen mensual')
    hoy = date.today()
    meses = sorted({str(m['fecha'])[:7] for m in movimientos} | {hoy.strftime('%Y-%m')}, reverse=True)
    mes = st.selectbox('Mes', meses)
    seleccion = [m for m in movimientos if str(m['fecha']).startswith(mes)]
    ingresos, gastos = importes_balance(seleccion)
    a, b, c = st.columns(3)
    a.markdown(indicador_financiero('Ingresos', ingresos, 'ingresos'), unsafe_allow_html=True)
    b.markdown(indicador_financiero('Gastos', gastos, 'gastos'), unsafe_allow_html=True)
    c.markdown(indicador_financiero('Ahorro', ingresos - gastos, 'ahorro'), unsafe_allow_html=True)
    def mostrar_quesito(periodo, clave):
        por_categoria = {}
        for m in movimientos:
            if es_excluido(m) or not str(m['fecha']).startswith(periodo):
                continue
            nombre = categoria_movimiento(m)
            if m['tipo'] == 'gasto':
                por_categoria[nombre] = por_categoria.get(nombre, 0) + float(m['importe'])
            elif m['tipo'] == 'ingreso' and nombre in CATEGORIAS_COMPENSACION:
                por_categoria[nombre] = por_categoria.get(nombre, 0) - float(m['importe'])
        if not por_categoria:
            st.info('No hay gastos registrados en este periodo.')
            return
        # Iconos vectoriales monocromos: heredan exactamente el color del sector.
        trazos_iconos = {
            'Alimentación': '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 2h3l3 13h13l3-10H5"/>',
            'Restaurantes y ocio': '<path d="M4 2v8m4-8v8m-4-4h4m-2 4v12M16 2c-4 4-4 11 0 12h3V2m0 12v8"/>',
            'Vivienda': '<path d="m2 11 10-9 10 9M4 10v12h16V10M9 22v-8h6v8"/>',
            'Suministros': '<path d="M9 18h6m-5 3h4M9 15c-7-5-3-13 3-13s10 8 3 13l-1 3h-4z"/>',
            'Coche y Transportes': '<path d="M4 16 6 8h12l2 8v5h-3v-3H7v3H4v-5zM4 16h16M6 8l2-4h8l2 4"/><circle cx="7.5" cy="15" r="1"/><circle cx="16.5" cy="15" r="1"/>',
            'Salud': '<path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/>',
            'Ropa y compras': '<path d="M4 9h16l-1 13H5L4 9zM9 10V6a3 3 0 0 1 6 0v4"/>',
            'Seguros e impuestos': '<path d="m12 2 9 4v6c0 6-4 9-9 11-5-2-9-5-9-11V6z"/>',
            'Regalos y donaciones': '<rect x="3" y="9" width="18" height="13" rx="1"/><path d="M2 9h20M12 9v13M12 9C3 9 5 2 9 3c2 0 3 3 3 6zm0 0c9 0 7-7 3-6-2 0-3 3-3 6z"/>',
            'Otros gastos': '<path d="M3 7 12 2l9 5v11l-9 5-9-5zM3 7l9 5 9-5m-9 5v11"/>',
            'Compras': '<path d="M3 7h18l-1.5 14h-15zM8 8V6a4 4 0 0 1 8 0v2"/>',
            'Colegio': '<path d="m2 9 10-6 10 6-10 6zM6 12v6c4 3 8 3 12 0v-6M22 9v8"/>',
            'Loterías': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h10M7 12h6M7 16h3M16 15l2 2"/>',
            'Dani': '<circle cx="12" cy="7" r="4"/><path d="M5 22v-4a7 7 0 0 1 14 0v4M9 14l3 4 3-4"/>',
            'Ropa y compras': '<path d="M4 9h16l-1 13H5L4 9zM9 10V6a3 3 0 0 1 6 0v4"/>',
            'Transporte': '<path d="M4 16 6 8h12l2 8v5h-3v-3H7v3H4v-5zM4 16h16"/>',
            'Gasolina': '<rect x="3" y="5" width="12" height="17" rx="1"/><rect x="5" y="8" width="8" height="6"/><path d="M15 10h2l3 3v6a2 2 0 0 0 4 0v-7l-3-4M3 22h13"/>',
            'Viajes': '<path d="M3 12 21 4l-6 17-4-7-8-2zM11 14l10-10"/>',
            'Sin categoría': '<circle cx="12" cy="12" r="10"/><path d="M9 9a3 3 0 0 1 6 0c0 3-3 3-3 6m0 3v1"/>'
        }
        def icono_svg(nombre, color, x, y, escala=1):
            trazos = trazos_iconos.get(nombre, trazos_iconos['Sin categoría'])
            return (f'<g transform="translate({x},{y}) scale({escala})" '
                    f'fill="none" stroke="{color}" stroke-width="2" '
                    f'stroke-linecap="round" stroke-linejoin="round">{trazos}</g>')
        datos_pie = pd.DataFrame([{'Categoría': k, 'Importe': v}
                                  for k, v in por_categoria.items() if v > 0])
        datos_pie = datos_pie.sort_values('Importe', ascending=False).reset_index(drop=True)
        total_pie = datos_pie['Importe'].sum()
        # SVG responsive: llamadas con líneas guía, sin recortes de Altair.
        import math
        from html import escape
        import streamlit.components.v1 as components

        # Donut limpio + leyenda alineada en filas. Las llamadas exteriores se
        # sustituyen por importes en la leyenda: evita cruces y superposiciones.
        cx, cy, radio_ext, radio_int = 240, 240, 170, 103
        angulo = -math.pi / 2
        sectores_svg = []
        filas_leyenda = []
        altura_fila = 32
        for indice, fila in datos_pie.iterrows():
            nombre = str(fila['Categoría'])
            importe = float(fila['Importe'])
            color = color_de(nombre)
            amplitud = 2 * math.pi * importe / total_pie
            fin = angulo + amplitud
            porcentaje = 100 * importe / total_pie
            titulo = f'{escape(nombre)}: {escape(dinero(importe))} ({porcentaje:.1f} %)'
            tooltip_attr = f'data-tooltip="{titulo}" style="cursor:pointer"'

            if amplitud >= 2 * math.pi - 1e-6:
                sectores_svg.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{radio_ext}" fill="{color}" {tooltip_attr}>'
                    f'<title>{titulo}</title></circle>')
            else:
                x1 = cx + radio_ext * math.cos(angulo)
                y1 = cy + radio_ext * math.sin(angulo)
                x2 = cx + radio_ext * math.cos(fin)
                y2 = cy + radio_ext * math.sin(fin)
                grande = int(amplitud > math.pi)
                sectores_svg.append(
                    f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} '
                    f'A {radio_ext} {radio_ext} 0 {grande} 1 {x2:.2f} {y2:.2f} Z" '
                    f'fill="{color}" stroke="#161616" stroke-width="1.5" {tooltip_attr}>'
                    f'<title>{titulo}</title></path>')
            y = 40 + indice * altura_fila
            filas_leyenda.append(
                f'<g {tooltip_attr}><title>{titulo}</title>'
                f'<rect x="490" y="{y-12}" width="13" height="13" rx="3" fill="{color}"/>'
                + icono_svg(nombre, color, 514, y-17, 0.75)
                + f'<text x="543" y="{y}" fill="#E5E7EB" font-size="15">'
                  f'{escape(nombre)}</text>'
                + f'<text x="760" y="{y}" fill="#F8FAFC" font-size="15" '
                  f'font-weight="600" text-anchor="start">{escape(dinero(importe))} ({porcentaje:.1f} %)</text>'
                + '</g>')
            angulo = fin

        altura_svg = max(490, 75 + len(filas_leyenda) * altura_fila)
        svg = (
            '<div style="width:100%;overflow-x:auto">'
            f'<svg id="pie-{clave}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1050 {altura_svg}" '
            'style="width:100%;max-width:1050px;height:auto;display:block;margin:auto" '
            'role="img" aria-label="Gastos por categoría e importes">'
            + ''.join(sectores_svg)
            + f'<circle cx="{cx}" cy="{cy}" r="{radio_int}" fill="#151515"/>'
            + f'<text x="{cx}" y="{cy+7}" text-anchor="middle" '
              f'font-size="24" font-weight="bold" fill="#FFFFFF">{escape(dinero(total_pie))}</text>'
            + ''.join(filas_leyenda)
            + '</svg>'
            + '<div id="tooltip" style="display:none;position:fixed;z-index:9999;'
              'pointer-events:none;background:#27272a;color:white;border:1px solid #71717a;'
              'border-radius:6px;padding:8px 11px;font:14px sans-serif;'
              'box-shadow:0 3px 15px #0009;white-space:nowrap"></div>'
            + '</div>'
            + '''<script>
              (function(){
                const tip=document.getElementById("tooltip");
                const svg=document.querySelector("svg[role=img]");
                if(!svg||!tip)return;
                const targets=svg.querySelectorAll("[data-tooltip]");
                targets.forEach(el=>{
                  el.addEventListener("pointerenter",ev=>{
                    tip.textContent=el.getAttribute("data-tooltip");
                    tip.style.display="block";
                  });
                  el.addEventListener("pointermove",ev=>{
                    const w=tip.offsetWidth,h=tip.offsetHeight;
                    tip.style.left=Math.max(4,Math.min(ev.clientX+14,innerWidth-w-4))+"px";
                    tip.style.top=Math.max(4,Math.min(ev.clientY+12,innerHeight-h-4))+"px";
                  });
                  el.addEventListener("pointerleave",()=>{tip.style.display="none"});
                });
              })();
              </script>'''
        )
        # En móvil el donut y la leyenda se apilan. No se reduce el SVG
        # de escritorio a una miniatura ilegible.
        from html import escape as html_escape
        import json
        escala_movil = 0.66
        donut_movil = (
            '<svg viewBox="0 0 480 480" class="donut-mobile" role="img" '
            'aria-label="Distribución de gastos por categoría">'
            + ''.join(sectores_svg)
            + f'<circle cx="{cx}" cy="{cy}" r="{radio_int}" fill="#151515"/>'
            + f'<text x="{cx}" y="{cy+8}" text-anchor="middle" fill="white" '
              f'font-size="25" font-weight="700">{html_escape(dinero(total_pie))}</text>'
            + '</svg>'
        )
        leyenda_movil = []
        for fila in datos_pie.itertuples(index=False):
            nombre = str(getattr(fila, '_0', fila[0]))
            importe = float(fila[1])
            color = color_de(nombre)
            porcentaje = 100 * importe / total_pie
            titulo = f'{nombre}: {dinero(importe)} ({porcentaje:.1f} %)'
            leyenda_movil.append(
                f'<div class="leyenda-fila" data-tooltip="{html_escape(titulo, quote=True)}">'
                f'<span class="marca" style="background:{color}"></span>'
                + '<span class="icono">'
                + '<svg viewBox="0 0 24 24">'
                + icono_svg(nombre, color, 0, 0, 1)
                + '</svg></span>'
                + f'<span class="categoria">{html_escape(nombre)}</span>'
                + f'<span class="importe">{html_escape(dinero(importe))} '
                  f'<small>({porcentaje:.1f} %)</small></span></div>'
            )
        # El tooltip usa click en móviles (sin depender de hover).
        movil_html = (
            '<style>'
            '.vista-escritorio{display:block}.vista-movil{display:none}'
            '@media(max-width:650px){.vista-escritorio{display:none}.vista-movil{display:block}}'
            '.donut-mobile{width:min(100%,340px);display:block;margin:0 auto 12px}'
            '.leyenda-fila{display:flex;align-items:center;gap:7px;padding:9px 3px;'
            'border-bottom:1px solid #333;cursor:pointer;color:#eee;font:13px sans-serif}'
            '.leyenda-fila .marca{width:10px;height:10px;border-radius:3px;flex-shrink:0}'
            '.leyenda-fila .icono{width:19px;height:19px;flex-shrink:0}'
            '.leyenda-fila .icono svg{width:19px;height:19px}'
            '.leyenda-fila .categoria{flex:1;min-width:0;overflow-wrap:anywhere}'
            '.leyenda-fila .importe{text-align:right;white-space:nowrap;font-weight:600}'
            '.leyenda-fila small{color:#aaa;font-size:11px}'
            '</style>'
            '<div class="vista-movil">' + donut_movil + ''.join(leyenda_movil) + '</div>'
        )
        # Reutilizamos el manejador de tooltip y lo adaptamos a toque.
        # Solo el HTML visible se inserta; no hay scroll horizontal en móvil.
        movil_html += '''<div id="tip-movil" style="display:none;position:fixed;z-index:1000;
            background:#292929;color:white;padding:9px 12px;border:1px solid #777;
            border-radius:7px;font:13px sans-serif;pointer-events:none"></div>
            <script>
            (function(){
              const tip=document.getElementById('tip-movil');
              const els=document.querySelectorAll('[data-tooltip]');
              function show(el,ev){
                tip.textContent=el.getAttribute('data-tooltip');tip.style.display='block';
                const x=(ev.clientX||innerWidth/2),y=(ev.clientY||120);
                tip.style.left=Math.max(5,Math.min(x+10,innerWidth-tip.offsetWidth-5))+'px';
                tip.style.top=Math.max(5,Math.min(y+10,innerHeight-tip.offsetHeight-5))+'px';
              }
              els.forEach(el=>{
                el.addEventListener('pointerenter',e=>show(el,e));
                el.addEventListener('pointermove',e=>show(el,e));
                el.addEventListener('pointerleave',()=>tip.style.display='none');
                el.addEventListener('click',e=>{show(el,e);setTimeout(()=>tip.style.display='none',2400)});
              });
            })();
            </script>'''
        # Dos iframes separados: la altura de la versión móvil ya no crea
        # un enorme hueco en escritorio (y viceversa).
        with st.container(key=f'quesito_escritorio_{clave}'):
            components.html(svg, height=altura_svg + 15, scrolling=False)
        with st.container(key=f'quesito_movil_{clave}'):
            components.html(movil_html, height=365 + len(leyenda_movil) * 40,
                            scrolling=False)
        st.caption(f'Total de gastos: **{dinero(total_pie)}**')

    st.subheader('Gastos del mes por categoría')
    mostrar_quesito(mes, 'mensual')
    st.subheader('Evolución anual')
    anos = sorted({int(str(m['fecha'])[:4]) for m in movimientos} | {hoy.year}, reverse=True)
    ano = st.selectbox('Año', anos, key='ano_resumen')
    filas_ano = []
    for numero_mes in range(1, 13):
        del_mes = [m for m in movimientos if str(m['fecha']).startswith(f'{ano}-{numero_mes:02d}')]
        i, g = importes_balance(del_mes)
        i, g = float(i), float(g)
        filas_ano.append({'Mes': ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'][numero_mes-1],
                         'Ingresos': i, 'Gastos': g, 'Ahorro': i-g})
    anual = pd.DataFrame(filas_ano)
    st.markdown(f'### Totales de {ano}')
    total_ingresos, total_gastos, total_ahorro = st.columns(3)
    total_ingresos.markdown(indicador_financiero('Ingresos anuales', anual['Ingresos'].sum(), 'ingresos'), unsafe_allow_html=True)
    total_gastos.markdown(indicador_financiero('Gastos anuales', anual['Gastos'].sum(), 'gastos'), unsafe_allow_html=True)
    total_ahorro.markdown(indicador_financiero('Ahorro anual', anual['Ahorro'].sum(), 'ahorro'), unsafe_allow_html=True)

    orden_meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    # Etiquetas en formato español: punto de miles y sin decimales.
    def etiqueta_euros(valor):
        return f"{valor:,.0f}".replace(',', '.') + ' €'

    # Posiciones numéricas compartidas: evita desplazamientos por xOffset y leyendas.
    anual['NumeroMes'] = range(1, 13)
    eje_meses = alt.Axis(
        values=list(range(1, 13)),
        labelExpr="['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][datum.value - 1]",
        labelAngle=0, domain=False, ticks=False, title=None,
    )
    escala_meses = alt.Scale(domain=[0.5, 12.5], nice=False, zero=False)
    eje_importes = alt.Axis(grid=False, domain=False, format=',.0f',
                           labelLimit=75, minExtent=85, maxExtent=85)
    datos_grafica = anual.melt(id_vars=['Mes', 'NumeroMes'],
                               value_vars=['Ingresos', 'Gastos'],
                               var_name='Tipo', value_name='Importe')
    datos_grafica['Posicion'] = datos_grafica['NumeroMes'] + datos_grafica['Tipo'].map(
        {'Ingresos': -0.19, 'Gastos': 0.19})
    datos_grafica['Etiqueta'] = datos_grafica['Importe'].map(etiqueta_euros)
    base = alt.Chart(datos_grafica).encode(
        x=alt.X('Posicion:Q', scale=escala_meses, axis=eje_meses),
        y=alt.Y('Importe:Q', title='Euros', axis=eje_importes),
        color=alt.Color('Tipo:N', sort=['Ingresos', 'Gastos'], title=None, legend=None,
                        scale=alt.Scale(domain=['Ingresos', 'Gastos'], range=['#0875d1', '#82c8fa'])),
        tooltip=[alt.Tooltip('Mes:N'), alt.Tooltip('Tipo:N'),
                 alt.Tooltip('Etiqueta:N', title='Importe')]
    )
    barras = base.mark_bar(size=32)
    etiquetas = base.transform_filter('datum.Importe > 0').mark_text(
        dy=-9, fontSize=11, color='#aebbd0'
    ).encode(text=alt.Text('Etiqueta:N'))
    grafica_ingresos_gastos = (barras + etiquetas).properties(height=360, title='Ingresos y gastos')
    # Ambas gráficas se dibujan dentro de un único gráfico concatenado.
    # Vega-Lite comparte así exactamente el área horizontal y la escala X.


    ahorro_datos = anual[['Mes', 'NumeroMes', 'Ahorro']].copy()
    ahorro_datos['Etiqueta'] = ahorro_datos['Ahorro'].map(etiqueta_euros)
    ahorro_base = alt.Chart(ahorro_datos).encode(
        x=alt.X('NumeroMes:Q', scale=escala_meses, axis=eje_meses),
        y=alt.Y('Ahorro:Q', title='Euros', axis=eje_importes),
        color=alt.condition('datum.Ahorro >= 0', alt.value('#188b73'), alt.value('#d65b62')),
        tooltip=[alt.Tooltip('Mes:N'), alt.Tooltip('Etiqueta:N', title='Ahorro')]
    )
    ahorro_barras = ahorro_base.mark_bar(size=65)
    ahorro_etiquetas_pos = ahorro_base.transform_filter('datum.Ahorro > 0').mark_text(
        dy=-9, fontSize=11, color='#aebbd0'
    ).encode(text=alt.Text('Etiqueta:N'))
    ahorro_etiquetas_neg = ahorro_base.transform_filter('datum.Ahorro < 0').mark_text(
        dy=13, fontSize=11, color='#aebbd0'
    ).encode(text=alt.Text('Etiqueta:N'))
    grafica_ahorro = (ahorro_barras + ahorro_etiquetas_pos + ahorro_etiquetas_neg).properties(
        height=290, title='Ahorro mensual')
    graficas = alt.vconcat(grafica_ingresos_gastos, grafica_ahorro, spacing=35).resolve_scale(
        x='shared', y='independent', color='independent'
    ).configure_view(stroke=None)
    with st.container(key='graficas_escritorio'):
        st.altair_chart(graficas, use_container_width=True)

    # En móviles, los 12 meses no caben como 24 barras con etiquetas.
    # Usamos líneas para ingresos/gastos y barras para ahorro; valores al tocar.
    with st.container(key='graficas_movil'):
        st.caption('Ingresos y gastos por mes · toca un punto para ver el importe')
        movil = anual.melt(id_vars=['Mes', 'NumeroMes'],
                           value_vars=['Ingresos', 'Gastos'],
                           var_name='Tipo', value_name='Importe')
        movil['Etiqueta'] = movil['Importe'].map(etiqueta_euros)
        eje_movil = alt.Axis(values=list(range(1, 13)),
            labelExpr="['E','F','M','A','M','J','J','A','S','O','N','D'][datum.value - 1]",
            labelAngle=0, title=None, grid=False, domain=False)
        base_movil = alt.Chart(movil).encode(
            x=alt.X('NumeroMes:Q', scale=alt.Scale(domain=[0.5,12.5]),
                    axis=eje_movil),
            y=alt.Y('Importe:Q', axis=alt.Axis(title=None, grid=True, tickCount=4)),
            color=alt.Color('Tipo:N', scale=alt.Scale(
                domain=['Ingresos','Gastos'], range=['#0875d1','#82c8fa']),
                legend=alt.Legend(orient='bottom', title=None)),
            tooltip=[alt.Tooltip('Mes:N'), alt.Tooltip('Tipo:N'),
                     alt.Tooltip('Etiqueta:N', title='Importe')]
        )
        graf_movil = (base_movil.mark_line(strokeWidth=2.5) +
                      base_movil.mark_point(size=55, filled=True)).properties(height=220)
        st.altair_chart(graf_movil, use_container_width=True)
        st.caption('Ahorro mensual · verde positivo, rojo negativo')
        ahorro_movil = alt.Chart(ahorro_datos).mark_bar(size=15).encode(
            x=alt.X('NumeroMes:Q', scale=alt.Scale(domain=[0.5,12.5]),
                    axis=eje_movil),
            y=alt.Y('Ahorro:Q', axis=alt.Axis(title=None, grid=True, tickCount=4)),
            color=alt.condition('datum.Ahorro >= 0', alt.value('#188b73'),
                                alt.value('#d65b62')),
            tooltip=[alt.Tooltip('Mes:N'), alt.Tooltip('Etiqueta:N', title='Ahorro')]
        ).properties(height=190)
        st.altair_chart(ahorro_movil, use_container_width=True)

    st.subheader('Gastos del año por categoría')
    mostrar_quesito(str(ano), 'anual')

with tabs[1]:
    st.subheader('Nuevo movimiento')
    with st.form('nuevo_movimiento', clear_on_submit=True):
        fecha = st.date_input('Fecha', value=date.today())
        tipo = st.selectbox('Tipo', ['gasto', 'ingreso', 'transferencia'])
        concepto = st.text_input('Concepto')
        importe = st.number_input('Importe (€)', min_value=0.01, value=1.0, step=1.0, format='%.2f')
        opciones = {'Sin categoría': None}
        opciones.update({f"{c['nombre']} ({c['tipo']})": c['id'] for c in categorias if c['tipo'] == tipo})
        etiqueta = st.selectbox('Categoría', list(opciones))
        guardar = st.form_submit_button('Guardar movimiento')
    if guardar:
        if not concepto.strip():
            st.error('Introduce un concepto.')
        else:
            try:
                ejecutar('movimientos', 'insert', {
                    'usuario_id': st.session_state.usuario_id,
                    'fecha': fecha.isoformat(), 'tipo': tipo,
                    'concepto': concepto.strip(), 'importe': str(Decimal(str(importe)).quantize(Decimal('0.01'))),
                    'categoria_id': opciones[etiqueta], 'origen': 'manual'
                })
                st.success('Movimiento guardado.')
                st.rerun()
            except Exception:
                st.error('No se pudo guardar el movimiento.')

    st.subheader('Histórico de movimientos')
    if movimientos:
        st.caption('Modifica la categoría directamente en la tabla y pulsa «Guardar categorías». '
                   'Los cambios también se recordarán para próximas importaciones.')
        tamano_tabla = st.selectbox('Movimientos por página', [25, 50, 100, 250], index=1)
        total_paginas = max(1, (len(movimientos) + tamano_tabla - 1) // tamano_tabla)
        pagina_tabla = st.number_input('Página', min_value=1, max_value=total_paginas, value=1, step=1)
        lote_tabla = movimientos[(pagina_tabla-1)*tamano_tabla:pagina_tabla*tamano_tabla]
        mapa_tipo = {(c['tipo'], c['nombre']): c['id'] for c in categorias}
        cambios_por_guardar = []
        for tipo_tabla in ('gasto', 'ingreso', 'transferencia'):
            indices = [i for i, m in enumerate(lote_tabla) if m['tipo'] == tipo_tabla]
            if not indices:
                continue
            st.markdown(f"**{tipo_tabla.capitalize()}s**" if tipo_tabla != 'transferencia' else '**Transferencias**')
            opciones = ['Sin categoría'] + sorted(c['nombre'] for c in categorias if c['tipo'] == tipo_tabla)
            filas = pd.DataFrame([{
                'Fecha': lote_tabla[i]['fecha'], 'Concepto': lote_tabla[i]['concepto'],
                'Importe (€)': (
                    ('🔴 ' if tipo_tabla == 'gasto' else '🟢 ' if tipo_tabla == 'ingreso' else '⚪ ')
                    + f"{float(lote_tabla[i]['importe']):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
                    + ' €'
                ),
                'Excluir del balance': bool(lote_tabla[i].get('excluir_balance', False)),
                'Categoría': nombres_categorias.get(lote_tabla[i].get('categoria_id'), 'Sin categoría')
            } for i in indices])
            editado = st.data_editor(
                filas, hide_index=True, use_container_width=True,
                disabled=['Fecha', 'Concepto', 'Importe (€)'],
                column_config={'Categoría': st.column_config.SelectboxColumn(
                    'Categoría', options=opciones, required=True)},
                key=f'mov_editor_{tipo_tabla}_{pagina_tabla}_{tamano_tabla}'
            )
            for j, fila in editado.iterrows():
                m = lote_tabla[indices[j]]
                nuevo = fila['Categoría']
                anterior = nombres_categorias.get(m.get('categoria_id'), 'Sin categoría')
                nueva_exclusion = bool(fila['Excluir del balance'])
                if nuevo != anterior or nueva_exclusion != es_excluido(m):
                    cambios_por_guardar.append((m, None if nuevo == 'Sin categoría'
                                                else mapa_tipo.get((tipo_tabla, nuevo)),
                                                nueva_exclusion))
        if st.button('Guardar categorías', type='primary'):
            if not cambios_por_guardar:
                st.info('No hay categorías modificadas.')
            else:
                try:
                    reglas_nuevas = {}
                    for m, cat_id, excluir in cambios_por_guardar:
                        ejecutar('movimientos', 'update',
                                 {'categoria_id': cat_id, 'excluir_balance': excluir}, m['id'])
                        if cat_id and m['tipo'] in ('gasto', 'ingreso'):
                            clave = clave_aprendizaje(m['concepto'])
                            reglas_nuevas[(m['tipo'], clave)] = {
                                'usuario_id': st.session_state.usuario_id, 'tipo': m['tipo'],
                                'clave_concepto': clave, 'categoria_id': cat_id}
                    if reglas_nuevas:
                        try:
                            st.session_state.cliente.table('reglas_categorias').upsert(
                                list(reglas_nuevas.values()),
                                on_conflict='usuario_id,tipo,clave_concepto').execute()
                        except Exception:
                            st.warning('Categorías guardadas, pero no se pudieron guardar todas las reglas.')
                    st.success(f'Actualizados {len(cambios_por_guardar)} movimientos.')
                    st.rerun()
                except Exception as exc:
                    st.error(f'No se pudieron guardar todos los cambios: {exc}')
        st.caption('Para cambiar fecha, concepto, importe o eliminar un movimiento, utiliza la edición individual.')
        opciones_mov = {f"{m['fecha']} · {m['concepto']} · {dinero(m['importe'])} · {m['id'][:8]}": m for m in movimientos}
        with st.expander('Editar o eliminar un movimiento'):
            elegido = st.selectbox('Movimiento', list(opciones_mov))
            actual = opciones_mov[elegido]
            with st.form('editar_movimiento'):
                nueva_fecha = st.date_input('Fecha', value=date.fromisoformat(actual['fecha']), key='edit_fecha')
                nuevo_tipo = st.selectbox('Tipo', ['gasto', 'ingreso', 'transferencia'],
                    index=['gasto', 'ingreso', 'transferencia'].index(actual['tipo']), key='edit_tipo')
                nuevo_concepto = st.text_input('Concepto', value=actual['concepto'], key='edit_concepto')
                nuevo_importe = st.number_input('Importe (€)', min_value=0.01,
                    value=float(actual['importe']), format='%.2f', key='edit_importe')
                cats_validas = [c for c in categorias if c['tipo'] == nuevo_tipo]
                ids = [None] + [c['id'] for c in cats_validas]
                etiquetas = ['Sin categoría'] + [c['nombre'] for c in cats_validas]
                indice = ids.index(actual.get('categoria_id')) if actual.get('categoria_id') in ids else 0
                nueva_categoria = st.selectbox('Categoría', range(len(ids)), index=indice,
                    format_func=lambda i: etiquetas[i], key='edit_categoria')
                col_guardar, col_eliminar = st.columns(2)
                editar = col_guardar.form_submit_button('Guardar cambios')
                eliminar = col_eliminar.form_submit_button('Eliminar definitivamente')
            if editar:
                if not nuevo_concepto.strip():
                    st.error('El concepto no puede estar vacío.')
                else:
                    try:
                        ejecutar('movimientos', 'update', {
                            'fecha': nueva_fecha.isoformat(), 'tipo': nuevo_tipo,
                            'concepto': nuevo_concepto.strip(),
                            'importe': str(Decimal(str(nuevo_importe)).quantize(Decimal('0.01'))),
                            'categoria_id': ids[nueva_categoria]
                        }, actual['id'])
                        st.rerun()
                    except Exception:
                        st.error('No se pudieron guardar los cambios.')
            if eliminar:
                try:
                    ejecutar('movimientos', 'delete', identificador=actual['id'])
                    st.rerun()
                except Exception:
                    st.error('No se pudo eliminar el movimiento.')


with tabs[2]:
    st.subheader('Presupuestos mensuales')
    st.caption('Selecciona un mes y define los presupuestos solo para las categorías que quieras controlar. Cada mes conserva sus propios importes.')
    try:
        presupuestos = consultar('presupuestos')
    except Exception as exc:
        st.error(f'No se pudo acceder a la tabla presupuestos: {exc}')
        presupuestos = None
    if presupuestos is not None:
        cats_gasto = sorted([c for c in categorias if c['tipo'] == 'gasto'], key=lambda c: c['nombre'].casefold())
        ids_gasto = {str(c['id']) for c in cats_gasto}
        hoy_pres = date.today()
        meses_existentes = {str(p['mes'])[:7] for p in presupuestos if p.get('mes')}
        meses_movimientos = {str(m['fecha'])[:7] for m in movimientos}
        meses_opciones = sorted(meses_existentes | meses_movimientos |
                               {f'{ano}-{mes:02d}' for ano in range(hoy_pres.year-1, hoy_pres.year+2)
                                for mes in range(1, 13)}, reverse=True)
        mes_edicion = st.selectbox('Mes del presupuesto', meses_opciones,
                                   index=meses_opciones.index(hoy_pres.strftime('%Y-%m')),
                                   format_func=lambda x: f'{x[5:7]}/{x[:4]}', key='mes_edicion_presupuesto')
        presup_por_mes = {}
        for p in presupuestos:
            if p.get('mes'):
                clave = (str(p['mes'])[:7], str(p['categoria_id']))
                presup_por_mes[clave] = p

        with st.form('definir_presupuestos'):
            st.caption('Deja un importe en cero para quitar el presupuesto de esa categoría y mes.')
            nuevos_presupuestos = {}
            for cat in cats_gasto:
                anterior = presup_por_mes.get((mes_edicion, str(cat['id'])))
                importe_anterior = float(anterior['importe']) if anterior else 0.0
                st.markdown(
                    f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
                    f'background:{color_de(cat["nombre"])};margin-right:7px"></span>'
                    f'<span>{__import__("html").escape(cat["nombre"])}</span>',
                    unsafe_allow_html=True)
                nuevos_presupuestos[str(cat['id'])] = st.number_input(
                    'Presupuesto mensual', label_visibility='collapsed', min_value=0.0,
                    value=importe_anterior, step=10.0, format='%.2f',
                    key=f'presupuesto_{mes_edicion}_{cat["id"]}')
            guardar_pres = st.form_submit_button('Guardar presupuestos')
        if guardar_pres:
            try:
                for cat in cats_gasto:
                    cid = str(cat['id'])
                    nuevo = Decimal(str(nuevos_presupuestos[cid])).quantize(Decimal('0.01'))
                    anterior = presup_por_mes.get((mes_edicion, cid))
                    if anterior and nuevo == Decimal(str(anterior['importe'])):
                        continue
                    if anterior and nuevo == 0:
                        ejecutar('presupuestos', 'delete', identificador=anterior['id'])
                    elif anterior:
                        ejecutar('presupuestos', 'update', {'importe': str(nuevo),
                                                          'importe_mensual': str(nuevo)}, anterior['id'])
                    elif nuevo > 0:
                        ejecutar('presupuestos', 'insert', {
                            'usuario_id': st.session_state.usuario_id,
                            'categoria_id': cat['id'], 'mes': f'{mes_edicion}-01',
                            'importe': str(nuevo), 'importe_mensual': str(nuevo)})
                st.success('Presupuestos guardados.')
                st.rerun()
            except Exception as exc:
                st.error(f'No se pudieron guardar los presupuestos: {exc}')

        def activos_mes(mes_clave):
            return {cid: Decimal(str(p['importe'])) for (mes, cid), p in presup_por_mes.items()
                    if mes == mes_clave and cid in ids_gasto and Decimal(str(p['importe'])) > 0}

        def gasto_presupuestado(mes_clave, categoria_id=None):
            ids = set(activos_mes(mes_clave))
            acumulado = Decimal('0')
            for m in movimientos:
                if es_excluido(m) or not str(m['fecha']).startswith(mes_clave):
                    continue
                cid = str(m.get('categoria_id'))
                if cid not in ids or (categoria_id is not None and cid != categoria_id):
                    continue
                importe = Decimal(str(m['importe']))
                if m['tipo'] == 'gasto':
                    acumulado += importe
                elif m['tipo'] == 'ingreso' and categoria_movimiento(m) in CATEGORIAS_COMPENSACION:
                    acumulado -= importe
            return acumulado

        st.subheader('Comparación mensual por categoría')
        mes_pres = st.selectbox('Mes a comparar', meses_opciones,
                                index=meses_opciones.index(mes_edicion), key='mes_presupuestos',
                                format_func=lambda x: f'{x[5:7]}/{x[:4]}')
        activos = activos_mes(mes_pres)
        if not activos:
            st.info('No hay presupuestos definidos para este mes.')
        else:
            total_pres = sum(activos.values(), Decimal('0'))
            gasto_mes = gasto_presupuestado(mes_pres)
            a, b, c = st.columns(3)
            a.metric('Presupuesto mensual', dinero(total_pres))
            b.metric('Gasto real', dinero(gasto_mes))
            c.metric('Disponible', dinero(total_pres - gasto_mes))
            filas = []
            for cid, limite in activos.items():
                real = gasto_presupuestado(mes_pres, cid)
                diferencia = limite - real
                filas.append({'Categoría': nombres_categorias.get(cid, 'Sin categoría'),
                              'Presupuesto': float(limite), 'Gasto real': float(real),
                              'Diferencia': float(diferencia)})
            detalle = pd.DataFrame(filas).sort_values('Categoría')
            detalle_largo = detalle.melt(id_vars=['Categoría'], value_vars=['Presupuesto', 'Gasto real'],
                                         var_name='Tipo', value_name='Euros')
            colores_detalle = sorted(detalle['Categoría'].unique().tolist())
            graf_mes = alt.Chart(detalle_largo).mark_bar().encode(
                x=alt.X('Categoría:N', title=None, axis=alt.Axis(labelAngle=-30)),
                xOffset=alt.XOffset('Tipo:N', sort=['Presupuesto', 'Gasto real']),
                y=alt.Y('Euros:Q', title='Euros'),
                color=alt.Color('Categoría:N', scale=alt.Scale(
                    domain=colores_detalle, range=[color_de(c) for c in colores_detalle]), legend=None),
                opacity=alt.Opacity('Tipo:N', scale=alt.Scale(
                    domain=['Presupuesto', 'Gasto real'], range=[1.0, 0.48]),
                    legend=alt.Legend(title='Barra sólida: presupuesto · clara: gasto real')),
                tooltip=['Categoría:N', 'Tipo:N', alt.Tooltip('Euros:Q', format=',.2f')]
            ).properties(height=320)
            st.caption('Cada categoría utiliza el mismo color que en los gráficos circulares. Barra sólida: presupuesto; barra clara: gasto real.')
            st.altair_chart(graf_mes, use_container_width=True)
            detalle['Diferencia'] = detalle['Diferencia'].map(
                lambda v: ('🟢 ' if v > 0 else '🔴 ' if v < 0 else '⚪ ') + dinero(v))
            st.dataframe(detalle, hide_index=True, use_container_width=True,
                         column_config={'Presupuesto': st.column_config.NumberColumn(format='%.2f €'),
                                        'Gasto real': st.column_config.NumberColumn(format='%.2f €')})

        st.subheader('Evolución anual del presupuesto')
        anos_pres = sorted({int(x[:4]) for x in meses_opciones}, reverse=True)
        ano_pres = st.selectbox('Año', anos_pres, index=anos_pres.index(int(mes_pres[:4])), key='ano_presupuestos')
        nombres_meses = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
        evolucion = pd.DataFrame([{
            'Mes': nombres_meses[n-1], 'Número': n,
            'Presupuesto': float(sum(activos_mes(f'{ano_pres}-{n:02d}').values(), Decimal('0'))),
            'Gasto real': float(gasto_presupuestado(f'{ano_pres}-{n:02d}'))}
            for n in range(1, 13)])
        evolucion_larga = evolucion.melt(id_vars=['Mes','Número'],
                value_vars=['Presupuesto','Gasto real'], var_name='Tipo', value_name='Euros')
        evolucion_larga['Posición'] = evolucion_larga['Número'] + evolucion_larga['Tipo'].map(
            {'Presupuesto':-0.19,'Gasto real':0.19})
        graf_ano = alt.Chart(evolucion_larga).mark_bar(size=26).encode(
            x=alt.X('Posición:Q', scale=alt.Scale(domain=[0.5,12.5]),
                axis=alt.Axis(values=list(range(1,13)),
                    labelExpr="['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][datum.value - 1]",
                    title=None, labelAngle=0)),
            y=alt.Y('Euros:Q', title='Euros'),
            color=alt.Color('Tipo:N', scale=alt.Scale(domain=['Presupuesto','Gasto real'],
                            range=['#0875d1','#82c8fa']), title=None),
            tooltip=['Mes:N','Tipo:N',alt.Tooltip('Euros:Q', format=',.2f')]
        ).properties(height=330)
        st.altair_chart(graf_ano, use_container_width=True)
        st.caption('Cada mes muestra únicamente el presupuesto que hayas guardado para ese mes.')

with tabs[3]:
    st.subheader('Categorías personalizables')
    st.caption('Puedes crear las categorías iniciales que falten sin duplicar las existentes.')
    if st.button('Crear categorías iniciales que falten'):
        existentes_cat = {(c['tipo'], c['nombre'].casefold()) for c in categorias}
        pendientes = [{'usuario_id': st.session_state.usuario_id, 'nombre': nombre, 'tipo': tipo}
                      for tipo, nombres in CATEGORIAS_INICIALES.items() for nombre in nombres
                      if (tipo, nombre.casefold()) not in existentes_cat]
        if pendientes:
            try:
                ejecutar('categorias', 'insert', pendientes)
                st.success(f'Creadas {len(pendientes)} categorías.')
                st.rerun()
            except Exception as exc:
                st.error(f'No se pudieron crear las categorías: {exc}')
        else:
            st.info('Todas las categorías iniciales ya están creadas.')
    with st.form('nueva_categoria', clear_on_submit=True):
        nombre = st.text_input('Nombre de la categoría')
        tipo_categoria = st.selectbox('Tipo de categoría', ['gasto', 'ingreso'])
        crear = st.form_submit_button('Crear categoría')
    if crear:
        if not nombre.strip():
            st.error('Introduce un nombre.')
        else:
            try:
                ejecutar('categorias', 'insert', {
                    'usuario_id': st.session_state.usuario_id,
                    'nombre': nombre.strip(), 'tipo': tipo_categoria
                })
                st.rerun()
            except Exception:
                st.error('No se pudo crear la categoría. Puede que ya exista.')
    if categorias:
        st.dataframe(pd.DataFrame([{'Nombre': c['nombre'], 'Tipo': c['tipo']} for c in categorias]), hide_index=True, use_container_width=True)
        por_nombre = {f"{c['nombre']} ({c['tipo']})": c for c in categorias}
        seleccion_cat = st.selectbox('Categoría para editar o eliminar', list(por_nombre))
        cat = por_nombre[seleccion_cat]
        with st.form('editar_categoria'):
            nombre_editado = st.text_input('Nuevo nombre', value=cat['nombre'])
            c1, c2 = st.columns(2)
            renombrar = c1.form_submit_button('Renombrar')
            borrar = c2.form_submit_button('Eliminar categoría')
        if renombrar:
            if not nombre_editado.strip():
                st.error('Introduce un nombre.')
            else:
                try:
                    ejecutar('categorias', 'update', {'nombre': nombre_editado.strip()}, cat['id'])
                    st.rerun()
                except Exception:
                    st.error('No se pudo renombrar la categoría.')
        if borrar:
            try:
                ejecutar('categorias', 'delete', identificador=cat['id'])
                st.rerun()
            except Exception:
                st.error('No se puede eliminar una categoría utilizada por movimientos o presupuestos.')


with tabs[4]:
    st.subheader('Importar extractos bancarios')
    st.caption('Se importan únicamente movimientos desde el 01/09/2026. Los anteriores permanecen guardados, pero ocultos. Las transferencias dudosas aparecen para que decidas si importarlas como transferencia, gasto o ingreso. Las operaciones de inversión siguen excluidas por defecto.')
    banco = st.selectbox('Banco', ['Ibercaja', 'Trade Republic'])
    archivo = st.file_uploader('Extracto', type=['xlsx'] if banco == 'Ibercaja' else ['pdf'], key='extracto')
    if archivo is not None:
        try:
            datos = archivo.getvalue()
            firma_archivo = (banco, archivo.name, len(datos), hash(datos))
            if st.session_state.get('firma_archivo') != firma_archivo:
                leidos = ibercaja(datos) if banco == 'Ibercaja' else trade_republic(datos)
                leidos = huellas(leidos)
                # Excluir de la importación cualquier movimiento previo al corte.
                leidos = [m for m in leidos if str(m.get('fecha', ''))[:10] >= '2026-09-01']
                st.session_state.firma_archivo = firma_archivo
                st.session_state.movimientos_importar = leidos
            leidos = st.session_state.movimientos_importar
            # Paginación para no superar el límite por defecto de Supabase (1000 filas).
            existentes = set()
            for inicio in range(0, len(leidos), 200):
                lote = [m['huella'] for m in leidos[inicio:inicio+200]]
                r = st.session_state.cliente.table('movimientos').select('huella_importacion').eq(
                    'usuario_id', st.session_state.usuario_id).in_('huella_importacion', lote).execute()
                existentes.update(x['huella_importacion'] for x in (r.data or []))
            nuevos = [m for m in leidos if m['huella'] not in existentes]
            st.write(f'Leídos: **{len(leidos)}** · Ya importados: **{len(leidos)-len(nuevos)}** · Nuevos: **{len(nuevos)}**')
            if nuevos:
                # Buscar posibles coincidencias con entradas manuales existentes.
                # El importe y tipo deben ser iguales; la fecha admite +/- 7 días.
                manuales = [m for m in movimientos if m.get('origen') == 'manual'
                           and m.get('tipo') in ('gasto', 'ingreso')]
                def coincidencias_manuales(nuevo):
                    if nuevo['tipo'] not in ('gasto', 'ingreso'):
                        return []
                    try:
                        fecha_n = date.fromisoformat(str(nuevo['fecha'])[:10])
                        importe_n = Decimal(str(nuevo['importe'])).quantize(Decimal('0.01'))
                    except (ValueError, ArithmeticError):
                        return []
                    candidatos = []
                    for manual in manuales:
                        if manual['tipo'] != nuevo['tipo']:
                            continue
                        if Decimal(str(manual['importe'])).quantize(Decimal('0.01')) != importe_n:
                            continue
                        try:
                            dias = abs((date.fromisoformat(str(manual['fecha'])[:10]) - fecha_n).days)
                        except ValueError:
                            continue
                        if dias > 7:
                            continue
                        similitud = SequenceMatcher(
                            None, clave_aprendizaje(str(nuevo['concepto'])),
                            clave_aprendizaje(str(manual['concepto']))).ratio()
                        candidatos.append((dias, -similitud, manual))
                    candidatos.sort(key=lambda t: (t[0], t[1]))
                    return [m for _, _, m in candidatos]

                coincidencias = {i: coincidencias_manuales(m)
                                 for i, m in enumerate(nuevos)}
                coincidencias = {i: c for i, c in coincidencias.items() if c}
                if coincidencias:
                    st.warning(
                        f'Hay {len(coincidencias)} movimientos del banco con posibles '
                        'coincidencias manuales. Confirma cada uno antes de importar. '
                        'La comparación utiliza tipo, importe exacto y fecha cercana (±7 días); '
                        'el concepto ayuda a ordenar las coincidencias.')
                categorias_por_tipo = {tipo: {c['nombre']: c['id'] for c in categorias if c['tipo'] == tipo}
                                       for tipo in ('gasto', 'ingreso')}
                reglas_guardadas = st.session_state.cliente.table('reglas_categorias').select(
                    'tipo,clave_concepto,categoria_id').eq('usuario_id', st.session_state.usuario_id).execute().data or []
                nombres_por_id = {c['id']: c['nombre'] for c in categorias}
                reglas = {(r['tipo'], r['clave_concepto']): nombres_por_id.get(r['categoria_id'])
                          for r in reglas_guardadas}
                sugerencias = []
                for m in nuevos:
                    base = sugerir_categoria(m['concepto'], m['tipo'], m['observacion'])
                    aprendida = reglas.get((m['tipo'], clave_aprendizaje(m['concepto'])))
                    sugerencias.append((aprendida, 'Aprendida') if aprendida else base)
                filas = pd.DataFrame([{
                    'Incluir': (True if 'BIZUM' in str(m['concepto']).upper() else m['incluir']),
                    'Fecha': m['fecha'], 'Banco': m['banco'],
                    'Concepto': m['concepto'], 'Importe (€)': float(m['importe']),
                    'Tipo': m['tipo'],
                    'Excluir del balance': False,
                    'Coincidencia manual': ('Revisar' if i in coincidencias else 'Sin coincidencia'),
                    'Decisión transferencia': ('Pendiente de decidir' if m['tipo'] == 'transferencia' else 'No aplica'),
                    'Categoría': sugerencias[i][0] if sugerencias[i][0] in categorias_por_tipo.get(m['tipo'], {}) else 'Sin categoría',
                    'Revisión': sugerencias[i][1], 'Aviso': m['observacion']
                } for i, m in enumerate(nuevos)])
                # Tablas independientes: cada desplegable muestra solo categorías
                # compatibles con el tipo de movimiento.
                revisiones = []
                for tipo_import in ('gasto', 'ingreso', 'transferencia'):
                    idx = [i for i, m in enumerate(nuevos) if m['tipo'] == tipo_import]
                    if not idx:
                        continue
                    st.markdown(f"**{tipo_import.capitalize()}s**" if tipo_import != 'transferencia'
                                else '**Transferencias**')
                    opciones = ['Sin categoría'] + sorted(categorias_por_tipo.get(tipo_import, {}))
                    parte = filas.iloc[idx].copy()
                    parte['Categoría'] = parte['Categoría'].where(
                        parte['Categoría'].isin(opciones), 'Sin categoría')
                    if tipo_import == 'transferencia':
                        parte['Incluir'] = True
                        st.caption('Confirma cada transferencia: puedes omitirla, conservarla sin efecto en el ahorro o contabilizarla como gasto/ingreso.')
                    editada = st.data_editor(
                        parte, hide_index=True, use_container_width=True,
                        disabled=['Fecha', 'Banco', 'Concepto', 'Importe (€)', 'Aviso',
                                  'Revisión', 'Tipo'],
                        column_config={
                            'Categoría': st.column_config.SelectboxColumn(
                                'Categoría', options=opciones, required=True),
                            'Decisión transferencia': st.column_config.SelectboxColumn(
                                'Decisión transferencia',
                                options=(['Pendiente de decidir', 'No importar',
                                          'Transferencia (no cuenta)', 'Gasto', 'Ingreso']
                                         if tipo_import == 'transferencia' else ['No aplica']),
                                required=True),
                            'Coincidencia manual': st.column_config.SelectboxColumn(
                                'Coincidencia manual',
                                options=['Sin coincidencia', 'Revisar',
                                         'Es el mismo: no importar',
                                         'Es distinto: importar'],
                                required=True),
                        },
                        key=f'revision_importacion_{firma_archivo[3]}_{tipo_import}_v2')
                    revisiones.append(editada)
                revisado = pd.concat(revisiones).sort_index() if revisiones else filas
                if coincidencias:
                    with st.expander('Ver posibles duplicados manuales', expanded=True):
                        for i, candidatos in coincidencias.items():
                            n = nuevos[i]
                            st.write(f"**Banco:** {n['fecha']} · {n['tipo']} · "
                                     f"{n['importe']} € · {n['concepto']}")
                            for m in candidatos[:3]:
                                st.caption(f"Manual: {m['fecha']} · {m['importe']} € · "
                                           f"{m['concepto']}")

                st.caption('Revisa especialmente las filas con «Revisar». Las categorías pueden cambiarse antes de guardar. Para asignarlas, créalas primero en la pestaña Configuración.')
                st.caption('Desmarca los movimientos que no quieras importar. Una transferencia conservada como transferencia no cuenta para el ahorro.')
                if st.button('Guardar movimientos seleccionados', type='primary'):
                    pendientes_transferencias = [
                        i for i, fila in revisado.iterrows()
                        if nuevos[i]['tipo'] == 'transferencia' and bool(fila['Incluir'])
                        and fila['Decisión transferencia'] == 'Pendiente de decidir'
                    ]
                    if pendientes_transferencias:
                        st.error(f'Hay {len(pendientes_transferencias)} transferencias pendientes de decidir. '
                                 'Elige una opción en la columna «Decisión transferencia».')
                        st.stop()
                    pendientes = [
                        i for i, fila in revisado.iterrows()
                        if i in coincidencias and bool(fila['Incluir'])
                        and fila['Coincidencia manual'] == 'Revisar'
                    ]
                    if pendientes:
                        st.error(f'Confirma las {len(pendientes)} coincidencias pendientes '
                                 'en la columna «Coincidencia manual».')
                        st.stop()
                    elegidos = []
                    for i, fila in revisado.iterrows():
                        if (not bool(fila['Incluir'])
                            or fila['Coincidencia manual'] == 'Es el mismo: no importar'
                            or fila['Decisión transferencia'] == 'No importar'):
                            continue
                        m = nuevos[i]
                        tipo_final = fila['Tipo']
                        if m['tipo'] == 'transferencia':
                            tipo_final = {
                                'Transferencia (no cuenta)': 'transferencia',
                                'Gasto': 'gasto',
                                'Ingreso': 'ingreso'
                            }[fila['Decisión transferencia']]
                        categoria_nombre = fila['Categoría']
                        categoria_id = categorias_por_tipo.get(tipo_final, {}).get(categoria_nombre)
                        if categoria_nombre != 'Sin categoría' and categoria_id is None:
                            st.error(f"La categoría «{categoria_nombre}» no existe para el tipo «{tipo_final}». Créala o selecciona otra.")
                            elegidos = None
                            break
                        elegidos.append({
                            'usuario_id': st.session_state.usuario_id,
                            'fecha': m['fecha'], 'concepto': m['concepto'],
                            'importe': str(m['importe']), 'tipo': tipo_final,
                            'origen': 'importacion', 'banco_origen': m['banco'],
                            'huella_importacion': m['huella'], 'categoria_id': categoria_id,
                            'excluir_balance': bool(fila['Excluir del balance'])
                        })
                    if elegidos is None:
                        pass
                    elif not elegidos:
                        st.warning('No hay movimientos seleccionados.')
                    else:
                        try:
                            for inicio in range(0, len(elegidos), 100):
                                st.session_state.cliente.table('movimientos').upsert(
                                    elegidos[inicio:inicio+100], on_conflict='usuario_id,huella_importacion',
                                    ignore_duplicates=True).execute()
                            # Aprender únicamente de filas confirmadas y categorías válidas.
                            reglas_nuevas = {}
                            for i, fila in revisado.iterrows():
                                if (not bool(fila['Incluir'])
                                    or fila['Coincidencia manual'] == 'Es el mismo: no importar'
                                    or fila['Decisión transferencia'] == 'No importar'
                                    or fila['Tipo'] not in ('gasto', 'ingreso')):
                                    continue
                                categoria_id = categorias_por_tipo.get(fila['Tipo'], {}).get(fila['Categoría'])
                                if categoria_id:
                                    clave = clave_aprendizaje(nuevos[i]['concepto'])
                                    reglas_nuevas[(fila['Tipo'], clave)] = {
                                        'usuario_id': st.session_state.usuario_id,
                                        'tipo': fila['Tipo'], 'clave_concepto': clave,
                                        'categoria_id': categoria_id
                                    }
                            if reglas_nuevas:
                                try:
                                    st.session_state.cliente.table('reglas_categorias').upsert(
                                        list(reglas_nuevas.values()),
                                        on_conflict='usuario_id,tipo,clave_concepto').execute()
                                except Exception:
                                    st.warning('Movimientos guardados, pero no se pudieron guardar las reglas de aprendizaje.')
                            st.success(f'Importación completada. Seleccionados: {len(elegidos)}. Los duplicados se omiten.')
                            st.rerun()
                        except Exception as exc:
                            st.error('No se pudo completar la importación. Verifica que has ejecutado la migración SQL.')
            else:
                st.info('No hay movimientos nuevos en este extracto.')
        except Exception as exc:
            st.error(f'Error al leer o consultar el extracto: {type(exc).__name__}: {str(exc)[:240]}')
