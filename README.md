# MyTE

Aplicación para **Windows** para trabajar en equipo: **chat, capturas y tareas** en un solo sitio.

Esta guía explica, paso a paso y sin necesidad de saber programar, cómo instalarla.

## Antes de empezar

- Un ordenador con **Windows 10 u 11** de 64 bits.
- **Conexión a Internet** (el instalador descarga cosas).
- Unos **700 MB libres** en la unidad donde vayas a instalar.
- No hace falta ser administrador.
- No hace falta tener Python ni instalar nada antes.

## Instalación paso a paso

### Paso 1. Descarga el instalador

1. A la derecha de esta página, en la sección **Releases**, pulsa la última versión.
2. Abajo, en **Assets**, pulsa **MyTE_Instalador.exe** para descargarlo.

### Paso 2. Ábrelo

Ve a tu carpeta de **Descargas** y haz doble clic en `MyTE_Instalador.exe`.

Es normal que Windows muestre una pantalla azul que dice **"Windows protegió su PC"**. Pasa con cualquier programa nuevo que no es de una gran empresa. Para continuar:

1. Pulsa **Más información**.
2. Pulsa **Ejecutar de todas formas**.

Si tu antivirus lo bloquea, mira su historial o cuarentena y permite el archivo. Si no te fías, puedes revisar el código: es el archivo `myte_installer.py` de este repositorio.

### Paso 3. Elige dónde instalarlo

Se abre una ventana llamada **MyTE — Instalador**. Arriba está el campo **Carpeta de instalación**.

- **En tu propio ordenador:** deja la carpeta que aparece por defecto.
- **En un ordenador que se restaura al reiniciar** (por ejemplo, con Deep Freeze o Reboot Restore, como en muchas aulas): la carpeta por defecto se borraría al reiniciar. Pulsa **Examinar…** y elige una unidad que no se restaure, como otro disco (`D:`) o una **memoria USB**. El instalador crea una carpeta llamada `MyTE` dentro de la que elijas.

Consejos para la carpeta:

- Usa una ruta corta, por ejemplo `D:\MyTE` o `E:\MyTE`. Las rutas largas dan problemas en Windows (el instalador avisa si la tuya lo es).
- No sirven las rutas de red.

### Paso 4. Instala MyTE

1. Deja marcada **"Crear acceso directo en el escritorio"** si quieres el icono en el escritorio.
2. Marca **"Crear también el acceso al servidor"** solo si vas a alojar el chat para tu equipo. Si no sabes qué es, déjala sin marcar.
3. Pulsa el botón verde **Instalar MyTE**.
4. Espera. Verás cómo van saliendo estos pasos:
   - Descargar Python
   - Verificar la firma de Python
   - Instalar Python (privado para MyTE)
   - Instalar PySide6 y dependencias
   - Copiar MyTE y crear accesos directos

   Puede tardar **varios minutos** y descarga unos 80 MB solo para las librerías. Es normal que parezca parado un rato en el paso de PySide6. No cierres la ventana. Si usas una memoria USB, no la saques hasta que termine.
5. Cuando aparezca **"¡Todo listo! Ya puedes abrir MyTE."**, pulsa **Abrir MyTE**.

### Paso 5. Usa MyTE

Hay dos formas de abrir MyTE:

- Con el acceso directo del **escritorio** o buscando **MyTE** en el **menú Inicio**.
- Con el archivo **`Abrir MyTE.bat`**, que está dentro de la carpeta donde instalaste. Haz doble clic sobre él. Funciona siempre, aunque la memoria USB cambie de letra.

## Si tu ordenador se restaura al reiniciar

Al reiniciar, el ordenador vuelve a como estaba y borra lo que se hizo en `C:`. Si instalaste MyTE en otra unidad, **MyTE sigue ahí**. Solo se pierden los accesos directos del menú Inicio y del escritorio. Después de cada reinicio, elige lo que te resulte más cómodo:

- **Abre `Abrir MyTE.bat`** en la carpeta de instalación. No necesita nada más.
- O **vuelve a ejecutar el instalador** y elige la misma carpeta. Esta vez tarda poco (Python y las librerías ya están) y recrea los accesos directos.

## Qué instala exactamente

Todo queda dentro de la carpeta que elegiste, sin tocar el resto del sistema:

- `py`: un Python propio de MyTE. No modifica tu Python ni el PATH.
- `app`: los archivos de la aplicación.
- `Abrir MyTE.bat` (y `Abrir MyTE Servidor.bat`, si lo marcaste).
- `install.log`: el registro de la instalación.

Además crea accesos directos en el menú Inicio (carpeta **MyTE**) y, si lo elegiste, en el escritorio.

El instalador comprueba que el Python que descarga está firmado por la **Python Software Foundation**. Si la firma no es válida, se detiene y no lo instala.

## Si algo falla

**El instalador dice que la carpeta no sirve.**
Lee el mensaje: puede ser que la unidad no exista, que no haya espacio, que la ruta sea muy larga o que no se pueda escribir en esa carpeta (por ejemplo, una memoria USB protegida contra escritura).

**Sale un error al descargar.**
Comprueba tu conexión a Internet, y que un antivirus, un cortafuegos o la red de tu centro no estén bloqueando `python.org` ni `pypi.org`. Pulsa **Reintentar**.

**La instalación falla en otro paso.**
Abre el archivo `install.log` que está dentro de la carpeta de instalación. Ahí queda escrito el error exacto. Si abres una *issue* en este repositorio, pega su contenido.

**No aparece el botón "Ejecutar de todas formas".**
Primero pulsa **Más información**; el botón aparece justo después.

## Actualizar MyTE

Descarga la última versión desde **Releases**, ejecuta el instalador y elige **la misma carpeta** de la vez anterior. Como Python y las librerías ya están instalados, esta vez tardará mucho menos.

## Desinstalar

MyTE no aparece en "Aplicaciones instaladas". Para quitarlo:

1. Borra la carpeta donde lo instalaste.
2. Borra la carpeta **MyTE** del menú Inicio y, si lo creaste, el acceso directo del escritorio.

## Alternativa: instalar sin el .exe

Si prefieres no ejecutar el `.exe`, puedes lanzar el instalador desde el código:

1. Instala Python desde <https://www.python.org/downloads/> marcando **"Add python.exe to PATH"**.
2. Pulsa el botón verde **Code → Download ZIP** de este repositorio y **extrae** el ZIP (no lo abras desde dentro).
3. Abre la carpeta extraída, haz clic en la barra de direcciones, escribe `cmd` y pulsa **Enter**.
4. Escribe `python myte_installer.py` y pulsa **Enter**.
5. Sigue el paso 3 de esta guía.

Los cuatro archivos (`myte_installer.py`, `myte_client.py`, `myte_server.py` y `myte.ico`) tienen que estar en la misma carpeta.
