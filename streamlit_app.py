import streamlit as st
import pandas as pd
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
                filas = pd.DataFrame([{
                    'Incluir': (True if 'BIZUM' in str(m['concepto']).upper() else m['incluir']), 'Fecha': m['fecha'], 'Banco': m['banco'],
                    'Concepto': m['concepto'], 'Importe (€)': float(m['importe']),
                    'Tipo': m['tipo'], 'Aviso': m['observacion']
                } for m in nuevos])
                revisado = st.data_editor(filas, hide_index=True, use_container_width=True,
                    disabled=['Fecha', 'Banco', 'Concepto', 'Importe (€)', 'Aviso'],
                    column_config={'Tipo': st.column_config.SelectboxColumn(
                        'Tipo', options=['gasto', 'ingreso', 'transferencia'], required=True)},
                    key=f'revision_importacion_{firma_archivo[3]}_bizum_v2')
                st.caption('Desmarca los movimientos que no quieras importar. Las transferencias no cuentan para el ahorro.')
                if st.button('Guardar movimientos seleccionados', type='primary'):
                    elegidos = []
                    for i, fila in revisado.iterrows():
                        if not bool(fila['Incluir']):
                            continue
                        m = nuevos[i]
                        elegidos.append({
                            'usuario_id': st.session_state.usuario_id,
                            'fecha': m['fecha'], 'concepto': m['concepto'],
                            'importe': str(m['importe']), 'tipo': fila['Tipo'],
                            'origen': 'importacion', 'banco_origen': m['banco'],
                            'huella_importacion': m['huella']
                        })
                    if not elegidos:
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
