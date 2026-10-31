"""Certificados del equipo para firmar: almacenes de Windows y de los navegadores, tarjetas y archivos."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from blackpdf.core import certstore
from blackpdf.core.signatures import SignOptions, list_signatures, sign_pdf

from .conftest import build_pdf

PASSWORD = "clave segura"
ROOT = Path(__file__).resolve().parent.parent


def _nss_tool(name: str) -> str | None:
    """certutil y pk12util (paquete libnss3-tools); en desarrollo pueden estar sin instalar en build/."""
    found = shutil.which(name)
    if found:
        return found
    local = ROOT / "build" / "nss-tools" / "usr" / "bin" / name
    return str(local) if local.is_file() else None


needs_nss = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not _nss_tool("pk12util") or not certstore.softokn_path(),
    reason="hacen falta NSS y sus herramientas (libnss3-tools)",
)


def _name(common: str, organization: str = "") -> x509.Name:
    parts = [x509.NameAttribute(NameOID.COUNTRY_NAME, "ES")]
    if organization:
        parts.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization))
    parts.append(x509.NameAttribute(NameOID.COMMON_NAME, common))
    return x509.Name(parts)


def _issue(subject: x509.Name, key, issuer: x509.Name, issuer_key, *, ca: bool = False, days_after: int = 365):
    now = dt.datetime.now(dt.timezone.utc)
    usage = (x509.KeyUsage(False, False, False, False, False, True, True, False, False) if ca else
             x509.KeyUsage(True, True, False, False, False, False, False, False, False))
    return (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=days_after))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(usage, critical=True)
        .sign(issuer_key, hashes.SHA256())
    )


def _identity_files(tmp_path: Path, key_kind: str = "rsa"):
    """Un certificado emitido por una autoridad, como los de la FNMT: (p12, autoridad, certificado)."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = _name("AC PRUEBAS USUARIOS", "BLACKPDF PRUEBAS")
    ca = _issue(ca_name, ca_key, ca_name, ca_key, ca=True)
    key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) if key_kind == "rsa"
           else ec.generate_private_key(ec.SECP256R1()))
    cert = _issue(_name("PÉREZ RUIZ ANA - 11111111H"), key, ca_name, ca_key)
    data = pkcs12.serialize_key_and_certificates(
        b"ana", key, cert, [ca], serialization.BestAvailableEncryption(PASSWORD.encode()))
    path = tmp_path / f"ana_{key_kind}.p12"
    path.write_bytes(data)
    return path, ca, cert


def _nss_database(folder: Path, p12: Path, primary_password: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    certutil, pk12util = _nss_tool("certutil"), _nss_tool("pk12util")
    if primary_password:
        secret = folder.parent / f"{folder.name}.pw"
        secret.write_text(primary_password)
        subprocess.run([certutil, "-N", "-d", f"sql:{folder}", "-f", str(secret)], check=True, capture_output=True)
        subprocess.run([pk12util, "-i", str(p12), "-d", f"sql:{folder}", "-W", PASSWORD, "-k", str(secret)],
                       check=True, capture_output=True)
    else:
        subprocess.run([certutil, "-N", "-d", f"sql:{folder}", "--empty-password"], check=True, capture_output=True)
        subprocess.run([pk12util, "-i", str(p12), "-d", f"sql:{folder}", "-W", PASSWORD, "-K", ""],
                       check=True, capture_output=True)
    return folder


def _sign_and_check(tmp_path: Path, identity, password: str = "") -> None:
    output = tmp_path / f"firmado_{identity.source}.pdf"
    sign_pdf(build_pdf(pages=2), output, identity, password, SignOptions(reason="Pruebas"))
    status = list_signatures(output)[0]
    assert status.intact and status.valid and status.covers_whole_file
    assert "PÉREZ RUIZ ANA" in status.signer


# ---------------------------------------------------------------------------
# Lo común
# ---------------------------------------------------------------------------
def test_chain_and_what_can_sign(tmp_path):
    _, ca, cert = _identity_files(tmp_path)
    ca_der, der = ca.public_bytes(serialization.Encoding.DER), cert.public_bytes(serialization.Encoding.DER)
    (tmp_path / "otro").mkdir()
    other = _identity_files(tmp_path / "otro")[1]
    assert certstore.chain_for(der, [other.public_bytes(serialization.Encoding.DER), ca_der]) == [ca_der], \
        "solo la autoridad que lo firmó de verdad, aunque otra se llame igual"
    assert certstore.chain_for(der, []) == []
    assert certstore.can_sign(cert) and not certstore.can_sign(ca)
    info = certstore.certificate_info(cert)
    assert info.name.startswith("PÉREZ RUIZ ANA") and info.issuer == "AC PRUEBAS USUARIOS" and not info.expired


def test_find_certificate_files(tmp_path):
    (tmp_path / "Descargas" / "fnmt").mkdir(parents=True)
    (tmp_path / "Descargas" / "fnmt" / "Certificado.PFX").write_bytes(b"x")
    (tmp_path / "Descargas" / "copia.p12").write_bytes(b"x")
    (tmp_path / "Descargas" / "foto.jpg").write_bytes(b"x")
    (tmp_path / "Descargas" / ".oculta").mkdir()
    (tmp_path / "Descargas" / ".oculta" / "no.p12").write_bytes(b"x")
    (tmp_path / "Descargas" / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "Descargas" / "a" / "b" / "c" / "lejos.p12").write_bytes(b"x")
    found = [Path(p).name for p in certstore.find_certificate_files([str(tmp_path / "Descargas")])]
    assert found == ["copia.p12", "Certificado.PFX"]
    files = certstore.list_identities(files=[str(tmp_path / "Descargas" / "copia.p12"), str(tmp_path / "no.p12")],
                                      folders=[str(tmp_path / "Descargas")], home=str(tmp_path))
    names = [i.detail for i in files if i.source == "file"]
    assert names == ["copia.p12", "Certificado.PFX"], "sin repetir ni archivos que ya no están"
    assert all(i.secret == "file" for i in files if i.source == "file")


def test_file_identity_signs(tmp_path):
    p12, _, _ = _identity_files(tmp_path)
    identity = certstore.Identity("file", str(p12), "file", p12.name, secret="file")
    with pytest.raises(certstore.CertificatePasswordError):
        _sign_and_check(tmp_path, identity, "mala")
    _sign_and_check(tmp_path, identity, PASSWORD)


# ---------------------------------------------------------------------------
# Firefox, Chrome (NSS)
# ---------------------------------------------------------------------------
@needs_nss
def test_browser_certificates_are_found_and_sign(tmp_path):
    home = tmp_path / "casa"
    p12, ca, _ = _identity_files(tmp_path)
    _nss_database(home / ".pki" / "nssdb", p12)
    _nss_database(home / ".mozilla" / "firefox" / "abcd1234.default-release", _identity_files(tmp_path, "ec")[0])
    found = [i for i in certstore.list_identities(home=str(home)) if i.source == "nss"]
    assert sorted(i.origin for i in found) == ["chrome", "firefox"], "la autoridad no aparece: no sirve para firmar"
    chrome = next(i for i in found if i.origin == "chrome")
    assert chrome.info().name.startswith("PÉREZ RUIZ ANA") and chrome.secret == "" and chrome.detail == ""
    _sign_and_check(tmp_path, chrome)
    firefox = next(i for i in found if i.origin == "firefox")
    _sign_and_check(tmp_path, firefox)  # Clave EC (ECDSA).


@needs_nss
def test_browser_with_primary_password(tmp_path):
    home = tmp_path / "casa"
    p12, _, _ = _identity_files(tmp_path)
    _nss_database(home / ".pki" / "nssdb", p12, primary_password="principal")
    found = [i for i in certstore.list_identities(home=str(home)) if i.source == "nss"]
    assert len(found) == 1 and found[0].secret == "nss", "con contraseña principal hay que escribirla para firmar"
    with pytest.raises(certstore.CertificatePasswordError) as error:
        _sign_and_check(tmp_path, found[0], "otra")
    assert error.value.params["kind"] == "nss"
    _sign_and_check(tmp_path, found[0], "principal")


@needs_nss
def test_certificate_removed_from_browser(tmp_path):
    home = tmp_path / "casa"
    p12, _, _ = _identity_files(tmp_path)
    _nss_database(home / ".pki" / "nssdb", p12)
    identity = next(i for i in certstore.list_identities(home=str(home)) if i.source == "nss")
    gone = certstore.Identity(**{**identity.__dict__, "key_id": "00ff"})
    with pytest.raises(certstore.CertificateError):
        _sign_and_check(tmp_path, gone)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
PROVIDERS = {
    "cng": "-KeyAlgorithm RSA -KeyLength 2048 -Provider 'Microsoft Software Key Storage Provider'",
    "capi": "-KeyAlgorithm RSA -KeyLength 2048 -KeySpec Signature "
            "-Provider 'Microsoft Enhanced RSA and AES Cryptographic Provider'",
    "ecdsa": "-KeyAlgorithm ECDSA_nistP256 -Provider 'Microsoft Software Key Storage Provider'",
}


@pytest.mark.skipif(sys.platform != "win32", reason="almacén de certificados de Windows")
@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_windows_store_signs(tmp_path, provider):
    subject = f"PÉREZ RUIZ ANA - BlackPDF {provider}"
    script = (f"$c = New-SelfSignedCertificate -Subject 'CN={subject}' -CertStoreLocation Cert:\\CurrentUser\\My "
              f"-KeyUsage DigitalSignature,NonRepudiation -KeyExportPolicy NonExportable {PROVIDERS[provider]}; "
              "$c.Thumbprint")
    thumb = subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True,
                           text=True, encoding="utf-8").stdout.strip().splitlines()[-1]
    try:
        found = [i for i in certstore.list_identities() if i.source == "windows" and i.location == thumb.upper()]
        assert len(found) == 1 and found[0].info().name == subject and found[0].secret == ""
        _sign_and_check(tmp_path, found[0])
    finally:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Remove-Item -Path Cert:\\CurrentUser\\My\\{thumb} -DeleteKey"], capture_output=True)
