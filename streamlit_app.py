import streamlit as st
import pandas as pd
import altair as alt
import re
import unicodedata
from datetime import date
from decimal import Decimal
from supabase import create_client
from importadores import ibercaja, trade_republic, huellas

st.set_page_config(page_title='Control de gastos', page_icon='📊', layout='wide')


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
              'Transporte', 'Salud', 'Ropa y compras', 'Viajes',
              'Seguros e impuestos', 'Regalos y donaciones', 'Otros gastos'],
    'ingreso': ['Nómina', 'Intereses', 'Devoluciones', 'Otros ingresos']
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
            ('Transporte', ['REPSOL', 'CEPSA', 'MOEVE', 'GASOLINERA', 'RENFE', 'CABIFY', 'UBER', 'PARKING', 'METRO ']),
            ('Salud', ['FARMACIA', 'DENTISTA', 'CLINICA', 'HOSPITAL', 'OPTICA']),
            ('Ropa y compras', ['ZARA', 'PRIMARK', 'DECATHLON', 'H&M', 'CORTE INGLES']),
            ('Viajes', ['BOOKING', 'AIRBNB', 'HOTEL ', 'IBERIA', 'RYANAIR', 'VUELING']),
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
except Exception:
    st.error('No se pueden consultar los datos. Comprueba la conexión y las políticas RLS de Supabase.')
    st.stop()

nombres_categorias = {c['id']: c['nombre'] for c in categorias}
tabs = st.tabs(['Resumen', 'Movimientos', 'Categorías', 'Importar extractos'])

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
    ingresos = sum(Decimal(str(m['importe'])) for m in seleccion if m['tipo'] == 'ingreso')
    gastos = sum(Decimal(str(m['importe'])) for m in seleccion if m['tipo'] == 'gasto')
    a, b, c = st.columns(3)
    a.markdown(indicador_financiero('Ingresos', ingresos, 'ingresos'), unsafe_allow_html=True)
    b.markdown(indicador_financiero('Gastos', gastos, 'gastos'), unsafe_allow_html=True)
    c.markdown(indicador_financiero('Ahorro', ingresos - gastos, 'ahorro'), unsafe_allow_html=True)
    def mostrar_quesito(periodo, clave):
        por_categoria = {}
        for m in movimientos:
            if m['tipo'] == 'gasto' and str(m['fecha']).startswith(periodo):
                nombre = nombres_categorias.get(m.get('categoria_id'), 'Sin categoría')
                por_categoria[nombre] = por_categoria.get(nombre, 0) + float(m['importe'])
        if not por_categoria:
            st.info('No hay gastos registrados en este periodo.')
            return
        datos_pie = pd.DataFrame([{'Categoría': k, 'Importe': v}
                                  for k, v in por_categoria.items()])
        total_pie = datos_pie['Importe'].sum()
        sectores = alt.Chart(datos_pie).mark_arc(innerRadius=85, outerRadius=155).encode(
            theta=alt.Theta('Importe:Q', stack=True),
            color=alt.Color('Categoría:N', legend=alt.Legend(title=None, orient='right')),
            tooltip=['Categoría:N', alt.Tooltip('Importe:Q', title='Gasto (€)', format=',.2f'),
                     alt.Tooltip('Porcentaje:Q', format='.1f', title='%')]
        ).transform_calculate(Porcentaje=f'100 * datum.Importe / {total_pie}')
        centro = alt.Chart(pd.DataFrame([{'texto': dinero(total_pie)}])).mark_text(
            fontSize=21, fontWeight='bold', color='#FFFFFF', align='center', baseline='middle'
        ).encode(text='texto:N')
        st.altair_chart((sectores + centro).properties(height=340).configure_view(stroke=None),
                        use_container_width=True, key=f'quesito_{clave}')
        st.caption(f'Total de gastos: **{dinero(total_pie)}**')

    st.subheader('Gastos del mes por categoría')
    mostrar_quesito(mes, 'mensual')
    st.subheader('Evolución anual')
    anos = sorted({int(str(m['fecha'])[:4]) for m in movimientos} | {hoy.year}, reverse=True)
    ano = st.selectbox('Año', anos, key='ano_resumen')
    filas_ano = []
    for numero_mes in range(1, 13):
        del_mes = [m for m in movimientos if str(m['fecha']).startswith(f'{ano}-{numero_mes:02d}')]
        i = sum(float(m['importe']) for m in del_mes if m['tipo'] == 'ingreso')
        g = sum(float(m['importe']) for m in del_mes if m['tipo'] == 'gasto')
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
    st.altair_chart(graficas, use_container_width=True)

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
        tamano_tabla = st.selectbox('Movimientos por página', [100, 250, 500], index=1)
        total_paginas = max(1, (len(movimientos) + tamano_tabla - 1) // tamano_tabla)
        pagina_tabla = st.number_input('Página', min_value=1, max_value=total_paginas, value=1, step=1)
        lote_tabla = movimientos[(pagina_tabla-1)*tamano_tabla:pagina_tabla*tamano_tabla]
        filas = pd.DataFrame([{
            'Fecha': m['fecha'], 'Tipo': m['tipo'], 'Concepto': m['concepto'],
            'Importe (€)': float(m['importe']),
            'Categoría': nombres_categorias.get(m.get('categoria_id'), 'Sin categoría')
        } for m in lote_tabla])
        opciones_categorias = ['Sin categoría'] + sorted({c['nombre'] for c in categorias})
        tabla_editada = st.data_editor(
            filas, hide_index=True, use_container_width=True,
            disabled=['Fecha', 'Tipo', 'Concepto', 'Importe (€)'],
            column_config={'Categoría': st.column_config.SelectboxColumn(
                'Categoría', options=opciones_categorias, required=True)},
            key=f'movimientos_editor_{pagina_tabla}_{tamano_tabla}'
        )
        if st.button('Guardar categorías', type='primary'):
            mapa_tipo = {(c['tipo'], c['nombre']): c['id'] for c in categorias}
            cambios = []
            errores = []
            reglas_nuevas = {}
            for i, fila in tabla_editada.iterrows():
                m = lote_tabla[i]
                nueva = fila['Categoría']
                actual = nombres_categorias.get(m.get('categoria_id'), 'Sin categoría')
                if nueva == actual:
                    continue
                cat_id = None if nueva == 'Sin categoría' else mapa_tipo.get((m['tipo'], nueva))
                if nueva != 'Sin categoría' and not cat_id:
                    errores.append(f"{nueva} no es una categoría de {m['tipo']}")
                    continue
                cambios.append((m, cat_id))
                if cat_id and m['tipo'] in ('gasto', 'ingreso'):
                    clave = clave_aprendizaje(m['concepto'])
                    reglas_nuevas[(m['tipo'], clave)] = {
                        'usuario_id': st.session_state.usuario_id, 'tipo': m['tipo'],
                        'clave_concepto': clave, 'categoria_id': cat_id
                    }
            if errores:
                st.error('Hay categorías incompatibles: ' + '; '.join(sorted(set(errores))[:5]))
            elif not cambios:
                st.info('No hay categorías modificadas.')
            else:
                try:
                    for m, cat_id in cambios:
                        ejecutar('movimientos', 'update', {'categoria_id': cat_id}, m['id'])
                    if reglas_nuevas:
                        try:
                            st.session_state.cliente.table('reglas_categorias').upsert(
                                list(reglas_nuevas.values()),
                                on_conflict='usuario_id,tipo,clave_concepto').execute()
                        except Exception:
                            st.warning('Categorías actualizadas, pero no se guardaron todas las reglas aprendidas.')
                    st.success(f'Se han actualizado {len(cambios)} movimientos.')
                    st.rerun()
                except Exception as exc:
                    st.error(f'No se pudieron guardar todos los cambios: {type(exc).__name__}')
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
    st.subheader('Categorías personalizables')
    st.caption('Puedes cargar las 15 categorías acordadas sin duplicar las que ya existan.')
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
            st.info('Las 15 categorías ya están creadas.')
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


with tabs[3]:
    st.subheader('Importar extractos bancarios')
    st.caption('Revisa los movimientos antes de guardarlos. Las transferencias dudosas y operaciones de inversión se excluyen por defecto.')
    banco = st.selectbox('Banco', ['Ibercaja', 'Trade Republic'])
    archivo = st.file_uploader('Extracto', type=['xlsx'] if banco == 'Ibercaja' else ['pdf'], key='extracto')
    if archivo is not None:
        try:
            datos = archivo.getvalue()
            firma_archivo = (banco, archivo.name, len(datos), hash(datos))
            if st.session_state.get('firma_archivo') != firma_archivo:
                leidos = ibercaja(datos) if banco == 'Ibercaja' else trade_republic(datos)
                leidos = huellas(leidos)
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
                    'Categoría': sugerencias[i][0] if sugerencias[i][0] in categorias_por_tipo.get(m['tipo'], {}) else 'Sin categoría',
                    'Revisión': sugerencias[i][1], 'Aviso': m['observacion']
                } for i, m in enumerate(nuevos)])
                todas_categorias = ['Sin categoría'] + [n for nombres in CATEGORIAS_INICIALES.values() for n in nombres]
                todas_categorias += [c['nombre'] for c in categorias if c['nombre'] not in todas_categorias]
                revisado = st.data_editor(filas, hide_index=True, use_container_width=True,
                    disabled=['Fecha', 'Banco', 'Concepto', 'Importe (€)', 'Aviso', 'Revisión'],
                    column_config={
                        'Tipo': st.column_config.SelectboxColumn('Tipo', options=['gasto', 'ingreso', 'transferencia'], required=True),
                        'Categoría': st.column_config.SelectboxColumn('Categoría', options=todas_categorias, required=True),
                    }, key=f'revision_importacion_{firma_archivo[3]}_categorias_v1')
                st.caption('Revisa especialmente las filas con «Revisar». Las categorías pueden cambiarse antes de guardar. Para asignarlas, créalas primero en la pestaña Categorías.')
                st.caption('Desmarca los movimientos que no quieras importar. Las transferencias no cuentan para el ahorro.')
                if st.button('Guardar movimientos seleccionados', type='primary'):
                    elegidos = []
                    for i, fila in revisado.iterrows():
                        if not bool(fila['Incluir']):
                            continue
                        m = nuevos[i]
                        categoria_nombre = fila['Categoría']
                        categoria_id = categorias_por_tipo.get(fila['Tipo'], {}).get(categoria_nombre)
                        if categoria_nombre != 'Sin categoría' and categoria_id is None:
                            st.error(f"La categoría «{categoria_nombre}» no existe para el tipo «{fila['Tipo']}». Créala o selecciona otra.")
                            elegidos = None
                            break
                        elegidos.append({
                            'usuario_id': st.session_state.usuario_id,
                            'fecha': m['fecha'], 'concepto': m['concepto'],
                            'importe': str(m['importe']), 'tipo': fila['Tipo'],
                            'origen': 'importacion', 'banco_origen': m['banco'],
                            'huella_importacion': m['huella'], 'categoria_id': categoria_id
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
                                if not bool(fila['Incluir']) or fila['Tipo'] not in ('gasto', 'ingreso'):
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
