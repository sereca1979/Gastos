import streamlit as st
import pandas as pd
import altair as alt
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

with tabs[0]:
    st.subheader('Resumen mensual')
    hoy = date.today()
    meses = sorted({str(m['fecha'])[:7] for m in movimientos} | {hoy.strftime('%Y-%m')}, reverse=True)
    mes = st.selectbox('Mes', meses)
    seleccion = [m for m in movimientos if str(m['fecha']).startswith(mes)]
    ingresos = sum(Decimal(str(m['importe'])) for m in seleccion if m['tipo'] == 'ingreso')
    gastos = sum(Decimal(str(m['importe'])) for m in seleccion if m['tipo'] == 'gasto')
    a, b, c = st.columns(3)
    a.metric('Ingresos', dinero(ingresos))
    b.metric('Gastos', dinero(gastos))
    c.metric('Ahorro', dinero(ingresos - gastos))
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
    orden_meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    # Etiquetas en formato español: punto de miles y sin decimales.
    def etiqueta_euros(valor):
        return f"{valor:,.0f}".replace(',', '.') + ' €'

    datos_grafica = anual.melt(id_vars='Mes', value_vars=['Ingresos', 'Gastos'],
                               var_name='Tipo', value_name='Importe')
    datos_grafica['Etiqueta'] = datos_grafica['Importe'].map(etiqueta_euros)
    base = alt.Chart(datos_grafica).encode(
        x=alt.X('Mes:N', sort=orden_meses, title=None,
                axis=alt.Axis(labelAngle=0, domain=False, ticks=False),
                scale=alt.Scale(paddingInner=0.12, paddingOuter=0.06)),
        xOffset=alt.XOffset('Tipo:N', sort=['Ingresos', 'Gastos'],
                            scale=alt.Scale(paddingInner=0, paddingOuter=0)),
        y=alt.Y('Importe:Q', title='Euros',
                axis=alt.Axis(grid=False, domain=False, tickCount=5, format=',.0f', labelLimit=75, minExtent=85, maxExtent=85)),
        color=alt.Color('Tipo:N', sort=['Ingresos', 'Gastos'], title=None,
                        scale=alt.Scale(domain=['Ingresos', 'Gastos'], range=['#0875d1', '#82c8fa'])),
        tooltip=[alt.Tooltip('Mes:N'), alt.Tooltip('Tipo:N'),
                 alt.Tooltip('Etiqueta:N', title='Importe')]
    )
    barras = base.mark_bar(size=38)
    etiquetas = base.transform_filter('datum.Importe > 0').mark_text(
        dy=-9, fontSize=11, color='#aebbd0'
    ).encode(text=alt.Text('Etiqueta:N'))
    st.altair_chart((barras + etiquetas).properties(height=360).configure_view(stroke=None),
                    use_container_width=True)
    st.caption(f"Total {ano}: ingresos {dinero(anual['Ingresos'].sum())} · gastos {dinero(anual['Gastos'].sum())} · ahorro {dinero(anual['Ahorro'].sum())}")

    st.subheader('Ahorro mensual')
    ahorro_datos = anual[['Mes', 'Ahorro']].copy()
    ahorro_datos['Etiqueta'] = ahorro_datos['Ahorro'].map(etiqueta_euros)
    ahorro_base = alt.Chart(ahorro_datos).encode(
        x=alt.X('Mes:N', sort=orden_meses, title=None,
                axis=alt.Axis(labelAngle=0, domain=False, ticks=False),
                scale=alt.Scale(paddingInner=0.12, paddingOuter=0.06)),
        y=alt.Y('Ahorro:Q', title='Euros',
                axis=alt.Axis(grid=False, domain=False, format=',.0f', labelLimit=75, minExtent=85, maxExtent=85)),
        color=alt.condition('datum.Ahorro >= 0', alt.value('#188b73'), alt.value('#d65b62')),
        tooltip=[alt.Tooltip('Mes:N'), alt.Tooltip('Etiqueta:N', title='Ahorro')]
    )
    ahorro_barras = ahorro_base.mark_bar(size=76)
    ahorro_etiquetas_pos = ahorro_base.transform_filter('datum.Ahorro > 0').mark_text(
        dy=-9, fontSize=11, color='#aebbd0'
    ).encode(text=alt.Text('Etiqueta:N'))
    ahorro_etiquetas_neg = ahorro_base.transform_filter('datum.Ahorro < 0').mark_text(
        dy=13, fontSize=11, color='#aebbd0'
    ).encode(text=alt.Text('Etiqueta:N'))
    st.altair_chart((ahorro_barras + ahorro_etiquetas_pos + ahorro_etiquetas_neg)
                    .properties(height=290).configure_view(stroke=None),
                    use_container_width=True)
    if seleccion:
        por_categoria = {}
        for m in seleccion:
            if m['tipo'] == 'gasto':
                nombre = nombres_categorias.get(m.get('categoria_id'), 'Sin categoría')
                por_categoria[nombre] = por_categoria.get(nombre, 0) + float(m['importe'])
        if por_categoria:
            st.subheader('Gastos por categoría')
            st.bar_chart(pd.Series(por_categoria, name='Euros'))
    else:
        st.info('Todavía no hay movimientos en este mes.')

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
        filas = [{
            'Fecha': m['fecha'], 'Tipo': m['tipo'], 'Concepto': m['concepto'],
            'Importe (€)': float(m['importe']),
            'Categoría': nombres_categorias.get(m.get('categoria_id'), 'Sin categoría')
        } for m in movimientos]
        st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)
        opciones_mov = {f"{m['fecha']} · {m['concepto']} · {dinero(m['importe'])} · {m['id'][:8]}": m for m in movimientos}
        elegido = st.selectbox('Selecciona un movimiento para editar o eliminar', list(opciones_mov))
        actual = opciones_mov[elegido]
        with st.form('editar_movimiento'):
            nueva_fecha = st.date_input('Fecha', value=date.fromisoformat(actual['fecha']), key='edit_fecha')
            nuevo_tipo = st.selectbox('Tipo', ['gasto', 'ingreso', 'transferencia'], index=['gasto', 'ingreso', 'transferencia'].index(actual['tipo']), key='edit_tipo')
            nuevo_concepto = st.text_input('Concepto', value=actual['concepto'], key='edit_concepto')
            nuevo_importe = st.number_input('Importe (€)', min_value=0.01, value=float(actual['importe']), format='%.2f', key='edit_importe')
            cats_validas = [c for c in categorias if c['tipo'] == nuevo_tipo]
            ids = [None] + [c['id'] for c in cats_validas]
            etiquetas = ['Sin categoría'] + [c['nombre'] for c in cats_validas]
            indice = ids.index(actual.get('categoria_id')) if actual.get('categoria_id') in ids else 0
            nueva_categoria = st.selectbox('Categoría', range(len(ids)), index=indice, format_func=lambda i: etiquetas[i], key='edit_categoria')
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
                        'concepto': nuevo_concepto.strip(), 'importe': str(Decimal(str(nuevo_importe)).quantize(Decimal('0.01'))),
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
    else:
        st.info('No hay movimientos registrados.')

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
                sugerencias = [sugerir_categoria(m['concepto'], m['tipo'], m['observacion']) for m in nuevos]
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
                            st.success(f'Importación completada. Seleccionados: {len(elegidos)}. Los duplicados se omiten.')
                            st.rerun()
                        except Exception as exc:
                            st.error('No se pudo completar la importación. Verifica que has ejecutado la migración SQL.')
            else:
                st.info('No hay movimientos nuevos en este extracto.')
        except Exception as exc:
            st.error(f'Error al leer o consultar el extracto: {type(exc).__name__}: {str(exc)[:240]}')
