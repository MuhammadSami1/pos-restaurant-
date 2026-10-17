# Restaurant Billing

An offline Windows billing app for one restaurant and one PC. It opens directly
to billing, with no account, login, or splash screen.

## Run the app

Double-click **`dist\RestaurantBilling.exe`**, or double-click **`launch.cmd`**.
The executable includes Python and all libraries; the restaurant PC does not
need Python, uv, or an internet connection.

The nine starter dishes and prices come from AGENTS.md. Open **Settings → Dishes**
to change them. Dish changes appear immediately on the billing screen.
Choose the restaurant logo in **Settings → General**; it also appears beside
the restaurant name in the billing header after saving.

For development with uv installed:

```powershell
uv sync --locked
uv run python main.py
```

This workspace also includes a local uv installation:

```powershell
.\.bootstrap\bin\uv.exe run --offline --cache-dir .uv-cache python main.py
```

## Daily use

- Tap a dish to add one. Hold it for quantity presets or a typed quantity.
- Count dishes use whole quantities; weight dishes use kilograms. Edit a bill
  line with **+ / − / ✎ / ✕** before printing.
- A dish's corner **✎** edits its menu details; **✕** deletes it from the menu.
  Existing bill lines retain their original names, prices, and selling types.
- Choose Dine-in, Takeaway, or Delivery. Table/waiter and customer details are
  optional; enable their receipt sections in Bill Layout.
- Discount accepts rupees or a percentage. Tax applies to the food subtotal;
  the discount then applies to food plus tax. For Rs. 3,470 with 16% tax and
  10% discount: tax is Rs. 555.20, discount is Rs. 402.52, total is Rs. 3,622.68.
  Calculations use decimal arithmetic and round to two decimal places.
- **Delivery** switches delivery on or off. Its rate starts from **Settings →
  General → Delivery charge**. Use **−10 / +10** or type a different charge for
  the current order. It is added after discount, shown separately on the slip,
  and saved with the bill. Switching delivery off removes the charge.
- **Print Bill** saves one sale and sends it to the printer. **Enter** prints;
  **Esc** asks to clear the unprinted order.
- **Reprint last** prints the saved sale again without increasing sales.
- **Delete last bill**, or **Sales → a bill → Delete bill**, voids the sale after
  confirmation. Its audit record remains in SQLite and its amount disappears
  from daily totals. Bill numbers are never reused within a day.
- **Sales** opens on today. Use the calendar, previous/next day, or YYYY-MM-DD
  entry for another day. All dishes sold on the selected day are shown with
  quantities and ranked by item sales revenue before whole-bill discounts/tax.

If printing fails, the bill remains saved and the error explains how to retry.
Check the paper before retrying: a printer can accept part of a receipt before
reporting a failure. Use Reprint, rather than creating the same order again.
Voiding a bill does not cancel a job already in the Windows print queue.

## Printer setup

Connect the thermal printer, install its Windows driver, and open
**Settings → General**. Choose **80 mm** or **58 mm**.

**Windows driver:** select Windows driver and copy the exact printer name from
Windows **Settings → Printers & scanners**. In its Printing preferences, select
receipt/roll/continuous paper. Enable the cutter there if the driver offers it.
Use **Test print** to verify the configured device. Test slips never create sales.

**Direct USB:** select USB and enter the VID/PID from Device Manager → device
Properties → Details → Hardware Ids, for example `0x0483` and `0x5740`. The
printer must support ESC/POS and have a compatible WinUSB/libusb interface.
The executable includes libusb. If USB cannot open, the app tries the configured
Windows driver in raw ESC/POS mode before sending any receipt bytes. A failure
after printing starts never triggers an automatic second print.

**Urdu and other Unicode:** configure the Windows printer name even when USB is
selected. These receipts automatically use Windows' native text shaping and
printer driver, so joined Urdu letters do not depend on a printer code page.
Only the restaurant logo is sent as a graphic; dish photos never print.

**Settings → Bill Layout** controls optional sections, restaurant details,
multiline footer, and up to two promo lines. Preview and Test print use the
current form values, including unsaved edits. Item content and totals cannot be
edited as a template. The Windows preview shares its drawing/layout with
Windows printing. Direct ESC/POS uses the same wrapped content and emphasis;
the device firmware determines its physical font appearance.

USB/printer-driver availability, roll handling, cutter behavior, and physical
output must be checked on the restaurant's actual printer. Automated tests use
fake printer transports and do not certify a particular device.

## Data and photos

On Windows all persistent data lives in:

```text
%LOCALAPPDATA%\restaurant-billing\
  restaurant.db
  config.json
  images\
```

Photos and logos are copied here when selected, so moving the original image
does not break the menu. Missing or damaged photos fall back to text buttons.
Data stays outside the executable and survives rebuilding or replacing it.
For a backup, close the app and copy this entire folder. There are no export,
network, telemetry, or cloud features.

Tests/development can set `RESTAURANT_BILLING_DATA_DIR` to an isolated writable
directory. On non-Windows systems, the default development data directory is
the project folder. Windows remains the supported packaging/printing target.

SQLite uses WAL mode, full synchronization, and transactions. Each saved bill
contains a snapshot of its items; menu edits do not change historical sales.
Settings save through an atomic file replacement. A damaged manually edited
config is preserved as `config.damaged-*.json` before defaults are recovered.

## Build and verify

Double-click **`build.cmd`**, or run:

```powershell
uv run python -m PyInstaller --noconfirm --clean RestaurantBilling.spec
uv run python -m unittest discover -s tests -v
uv run python tests/ui_smoke.py
uv run python tests/package_smoke.py
```

The build outputs `dist\RestaurantBilling.exe`. The spec bundles CustomTkinter,
Tcl/Tk, ESC/POS capabilities, Windows printing support, and libusb. It does not
bundle the restaurant database or the owner's photos.

The unit suite checks transaction rollback, concurrent startup/numbering,
daily resets, voids, rounding/validation, snapshots, photo/config persistence,
receipt widths/toggles, Urdu previews, USB fallback, and uncertain print errors.
The UI smoke script briefly opens real windows with a temporary database and a
fake printer, verifies the billing/settings/sales flows, and saves screenshots
under `.test-data`. The package smoke script launches and closes the executable
using a temporary data folder. Neither sends a job to a physical printer.
