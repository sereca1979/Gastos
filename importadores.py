"""Lectores de extractos Ibercaja (xlsx) y Trade Republic (PDF)."""
import hashlib
import io
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

import fitz
from openpyxl import load_workbook

MESES = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
         'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12}
DINERO = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$|^-?\d+,\d{2}$')


def euros(texto):
    return Decimal(str(texto).replace('\xa0', '').replace('€', '').replace('.', '').replace(',', '.').strip())


def normalizar(texto):
    texto = unicodedata.normalize('NFKC', str(texto or '')).upper().strip()
    return re.sub(r'\s+', ' ', texto)


def clasificar(banco, concepto, descripcion, importe, tipo_banco=''):
    s = normalizar(concepto + ' ' + descripcion + ' ' + tipo_banco)
    t = normalizar(tipo_banco)
    # Nunca se consideran consumo las operaciones bursátiles.
    if banco == 'Trade Republic' and (t == 'OPERAR' or any(x in s for x in (
            'SAVINGS PLAN EXECUTION', 'SECURITIES TRANSACTION', 'STOCK PURCHASE',
            'STOCK SALE', 'DIVIDEND PAYMENT', 'CASH DIVIDEND'))):
        # Los dividendos son ingresos financieros; compras/ventas no.
        if 'DIVIDEND' in s and importe > 0:
            return 'ingreso', 'Dividendo', True
        return 'transferencia', 'Operación de inversión', False
    if any(x in s for x in ('TRANSFERENCIA ENTRE MIS CUENTAS', 'TRASPASO ENTRE CUENTAS',
                            'TRANSFERENCIA A TRADE REPUBLIC', 'TRANSFERENCIA DE TRADE REPUBLIC')):
        return 'transferencia', 'Transferencia propia', False
    if banco == 'Trade Republic' and t in ('TRANSFERENCIA', 'TRANSFER'):
        return 'transferencia', 'Revisar transferencia', False
    if 'INTERÉS' in s or 'INTEREST PAYMENT' in s or t == 'INTERÉS':
        return 'ingreso', 'Intereses', True
    if t == 'BONIFICACIÓN' or 'SAVEBACK' in s:
        return 'ingreso', 'Bonificación', True
    # Bizum enviado = gasto; Bizum recibido = ingreso.
    if 'BIZUM' in s:
        return ('ingreso' if importe > 0 else 'gasto'), '', True
    if 'TRANSFERENCIA' in s or 'TRASPASO' in s:
        return ('ingreso' if importe > 0 else 'gasto'), 'Revisar naturaleza', False
    return ('ingreso' if importe > 0 else 'gasto'), '', True


def construir(banco, fecha, concepto, descripcion, importe, saldo, tipo_banco=''):
    tipo, aviso, incluir = clasificar(banco, concepto, descripcion, importe, tipo_banco)
    return dict(banco=banco, fecha=fecha, concepto=(descripcion or concepto).strip(),
                importe=abs(importe), signo='+' if importe > 0 else '-',
                tipo=tipo, incluir=incluir, observacion=aviso,
                saldo=saldo, referencia=concepto, tipo_banco=tipo_banco)


def ibercaja(contenido):
    wb = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    hoja = wb.active
    filas = hoja.iter_rows(values_only=True)
    cabecera = None
    resultado = []
    for fila in filas:
        valores = [str(v or '').strip() for v in fila]
        if 'Fecha Operacion' in valores and 'Importe' in valores:
            cabecera = {nombre: valores.index(nombre) for nombre in (
                'Fecha Operacion', 'Concepto', 'Descripción', 'Referencia', 'Importe', 'Saldo')}
            continue
        if not cabecera:
            continue
        def campo(nombre):
            i = cabecera[nombre]
            return fila[i] if i < len(fila) else None
        try:
            fecha = campo('Fecha Operacion')
            if isinstance(fecha, datetime):
                fecha = fecha.date().isoformat()
            else:
                fecha = datetime.strptime(str(fecha).strip(), '%d/%m/%Y').date().isoformat()
            importe = euros(campo('Importe'))
            if not importe:
                continue
            concepto = str(campo('Concepto') or '')
            descripcion = str(campo('Descripción') or '')
            saldo = euros(campo('Saldo')) if campo('Saldo') is not None else None
            m = construir('Ibercaja', fecha, concepto, descripcion, importe, saldo)
            m['referencia'] = str(campo('Referencia') or '')
            resultado.append(m)
        except (ValueError, TypeError, ArithmeticError):
            continue
    wb.close()
    if cabecera is None:
        raise ValueError('No se ha encontrado la cabecera del extracto Ibercaja.')
    return resultado


def trade_republic(contenido):
    doc = fitz.open(stream=contenido, filetype='pdf')
    resultado = []
    for pagina in doc:
        palabras = pagina.get_text('words')
        # Fecha a la izquierda: día y mes en una línea; año debajo.
        anclas = []
        for w in palabras:
            x, y, _, _, t = w[:5]
            if not 72 <= x <= 80 or not re.fullmatch(r'\d{2}', t):
                continue
            mes = next((v[4] for v in palabras if abs(v[1] - y) < 1.7 and
                        82 <= v[0] <= 98 and v[4].lower()[:3] in MESES), None)
            anio = next((v[4] for v in palabras if 72 <= v[0] <= 80 and
                         5 <= v[1] - y <= 12 and re.fullmatch(r'20\d\d', v[4])), None)
            if mes and anio:
                try:
                    fecha = datetime(int(anio), MESES[mes.lower()[:3]], int(t)).date().isoformat()
                    anclas.append((y, fecha))
                except ValueError:
                    pass
        anclas.sort()
        for i, (inicio, fecha) in enumerate(anclas):
            fin = anclas[i + 1][0] if i + 1 < len(anclas) else pagina.rect.height - 65
            grupo = [w for w in palabras if inicio - 1 <= w[1] < fin - 1]
            # Recortar al saldo de la operación para evitar texto del pie de página.
            saldos_pos = [w[1] for w in grupo if 477 <= w[0] < 540 and DINERO.fullmatch(w[4])]
            if saldos_pos:
                grupo = [w for w in grupo if w[1] <= min(saldos_pos) + 8]
            def columna(a, b):
                v = [w for w in grupo if a <= w[0] < b]
                return ' '.join(w[4] for w in sorted(v, key=lambda z: (round(z[1] / 4), z[0])))
            tipo = columna(101, 151)
            descripcion = columna(152, 398).replace('null', '').strip()
            entradas = [w[4] for w in grupo if 398 <= w[0] < 439 and DINERO.fullmatch(w[4])]
            salidas = [w[4] for w in grupo if 440 <= w[0] < 475 and DINERO.fullmatch(w[4])]
            saldos = [w[4] for w in grupo if 477 <= w[0] < 540 and DINERO.fullmatch(w[4])]
            if not descripcion or (not entradas and not salidas):
                continue
            importe = euros(entradas[0]) if entradas else -euros(salidas[0])
            saldo = euros(saldos[0]) if saldos else None
            resultado.append(construir('Trade Republic', fecha, tipo, descripcion, importe, saldo, tipo))
    doc.close()
    if not resultado:
        raise ValueError('No se han podido leer movimientos del PDF de Trade Republic.')
    return resultado


def huellas(movimientos):
    """Clave estable independiente del archivo y de la categoría elegida.

    Saldo ayuda a diferenciar operaciones legítimas iguales en un día.
    Si no hay saldo, ordinal para ocurrencias completamente iguales.
    """
    apariciones = defaultdict(int)
    for m in movimientos:
        partes = (m['banco'], m['fecha'], m['signo'], str(m['importe']),
                  normalizar(m['concepto']), str(m['saldo']) if m['saldo'] is not None else '')
        apariciones[partes] += 1
        clave = '|'.join(partes) + '|' + str(apariciones[partes])
        m['huella'] = hashlib.sha256(clave.encode('utf-8')).hexdigest()
    return movimientos
