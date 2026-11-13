
import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Control de gastos",
    page_icon="💶",
    layout="wide"
)

@st.cache_resource
def conectar():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"]
    )

st.title("Control de gastos")

if "sesion" not in st.session_state:
    st.session_state.sesion = None

if st.session_state.sesion is None:
    st.subheader("Iniciar sesión")

    with st.form("login"):
        email = st.text_input("Correo electrónico")
        password = st.text_input(
            "Contraseña", type="password"
        )
        entrar = st.form_submit_button("Entrar")

    if entrar:
        try:
            respuesta = conectar().auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if respuesta.session:
                st.session_state.sesion = respuesta.session
                st.rerun()
            else:
                st.error("No se ha podido iniciar sesión.")

        except Exception:
            st.error("No se ha podido iniciar sesión. Comprueba las credenciales.")

else:
    st.success("Sesión iniciada correctamente")
    st.subheader("Panel de control")
    st.info("La aplicación está conectada. Próximamente mostraremos los gastos.")

    if st.button("Cerrar sesión"):
        try:
            conectar().auth.sign_out()
        finally:
            st.session_state.sesion = None
            st.rerun()
