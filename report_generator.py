"""
CarbonPass - Report Generator
生成符合 CBAM 规范的 XML 报告、PDF 报告、马来西亚 MRV 报告
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
from typing import Dict, List, Optional
import os
import uuid
import json

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


class ReportGenerator:
    """CBAM 合规报告生成器"""

    def __init__(self, reports_dir: str = None):
        if reports_dir is None:
            reports_dir = os.path.join(os.path.dirname(__file__), 'reports')
        self.reports_dir = reports_dir
        os.makedirs(reports_dir, exist_ok=True)

    def generate_cbam_xml(
        self,
        report_data: Dict,
        filename: str = None
    ) -> str:
        """
        生成 CBAM XML 格式报告

        参数:
            report_data: 包含报告数据的字典
            filename: 输出文件名（不含路径）

        返回:
            生成的 XML 文件路径
        """

        if filename is None:
            filename = f"CBAM_Report_{uuid.uuid4().hex[:8]}.xml"

        filepath = os.path.join(self.reports_dir, filename)

        # CBAM XML 结构 (基于欧盟委员会规范)
        root = ET.Element('CBAMReport', {
            'xmlns': 'urn:eu:cbam:report:v1.0',
            'version': '1.0'
        })

        # Report Header
        header = ET.SubElement(root, 'ReportHeader')

        report_id = ET.SubElement(header, 'ReportId')
        report_id.text = report_data.get('report_id', uuid.uuid4().hex[:12].upper())

        reporting_period = ET.SubElement(header, 'ReportingPeriod')
        reporting_period.text = report_data.get('reporting_period', '2026-Q1')

        report_date = ET.SubElement(header, 'ReportDate')
        report_date.text = datetime.now().strftime('%Y-%m-%d')

        # Reporter Information
        reporter = ET.SubElement(root, 'ReporterInformation')

        company_name = ET.SubElement(reporter, 'CompanyName')
        company_name.text = report_data.get('company_name', '')

        facility_id = ET.SubElement(reporter, 'FacilityId')
        facility_id.text = report_data.get('facility_id', '')

        facility_address = ET.SubElement(reporter, 'FacilityAddress')
        facility_address.text = report_data.get('facility_address', '')

        country = ET.SubElement(reporter, 'Country')
        country.text = report_data.get('country', 'MY')

        contact_person = ET.SubElement(reporter, 'ContactPerson')
        contact_person.text = report_data.get('contact_person', '')

        contact_email = ET.SubElement(reporter, 'ContactEmail')
        contact_email.text = report_data.get('contact_email', '')

        # Production Data
        production = ET.SubElement(root, 'ProductionData')

        product_type = ET.SubElement(production, 'ProductType')
        product_type.text = report_data.get('product_type', '')

        product_description = ET.SubElement(production, 'ProductDescription')
        product_description.text = report_data.get('product_description', '')

        quantity = ET.SubElement(production, 'Quantity')
        quantity.text = str(report_data.get('quantity', 0))

        unit = ET.SubElement(production, 'Unit')
        unit.text = report_data.get('unit', 'tonnes')

        production_date = ET.SubElement(production, 'ProductionPeriod')
        production_date.text = report_data.get('production_period', '')

        # Emissions Data
        emissions = ET.SubElement(root, 'EmissionsData')

        embedded_emissions = ET.SubElement(emissions, 'EmbeddedEmissions', {
            'unit': 'kg CO2'
        })
        embedded_emissions.text = str(report_data.get('embedded_emissions_kg', 0))

        emissions_per_unit = ET.SubElement(emissions, 'EmissionsPerUnit', {
            'unit': 'kg CO2 per tonne'
        })
        emissions_per_unit.text = str(report_data.get('emissions_per_tonne', 0))

        # Emission breakdown
        scope1 = ET.SubElement(emissions, 'Scope1Emissions', {'unit': 'kg CO2'})
        scope1.text = str(report_data.get('scope1_kg', 0))

        scope2 = ET.SubElement(emissions, 'Scope2Emissions', {'unit': 'kg CO2'})
        scope2.text = str(report_data.get('scope2_kg', 0))

        # Calculation methodology
        methodology = ET.SubElement(emissions, 'CalculationMethodology')
        methodology.text = report_data.get('methodology', 'Actual data based on GHG Protocol')

        emission_factor_source = ET.SubElement(emissions, 'EmissionFactorSource')
        emission_factor_source.text = report_data.get('factor_source', 'Malaysian grid factor + IPCC AR6')

        # Import Details
        imports = ET.SubElement(root, 'ImportDetails')

        eu_importers = ET.SubElement(imports, 'EUImporter')
        eu_importers.text = report_data.get('eu_importer', '')

        import_country = ET.SubElement(imports, 'ImportCountry')
        import_country.text = report_data.get('import_country', '')

        import_quantity = ET.SubElement(imports, 'ImportedQuantity')
        import_quantity.text = str(report_data.get('import_quantity', 0))

        # Verification (placeholder - to be completed by third party)
        verification = ET.SubElement(root, 'Verification')

        verified = ET.SubElement(verification, 'VerifiedByThirdParty')
        verified.text = 'No' if not report_data.get('verified', False) else 'Yes'

        verifier_name = ET.SubElement(verification, 'VerifierName')
        verifier_name.text = report_data.get('verifier_name', 'Not yet verified')

        verification_date = ET.SubElement(verification, 'VerificationDate')
        verification_date.text = report_data.get('verification_date', '')

        verification_standard = ET.SubElement(verification, 'VerificationStandard')
        verification_standard.text = 'ISO 14064-3'

        # Additional Information
        additional = ET.SubElement(root, 'AdditionalInformation')

        uncertainty = ET.SubElement(additional, 'UncertaintyPercent')
        uncertainty.text = str(report_data.get('uncertainty_percent', 0))

        data_quality = ET.SubElement(additional, 'DataQuality')
        data_quality.text = report_data.get('data_quality', 'Measured')

        remarks = ET.SubElement(additional, 'Remarks')
        remarks.text = report_data.get('remarks', 'Report generated by CarbonPass v1.0')

        # Footer
        footer = ET.SubElement(root, 'ReportFooter')

        generated_by = ET.SubElement(footer, 'GeneratedBy')
        generated_by.text = 'CarbonPass - Carbon Compliance Tool'

        generator_version = ET.SubElement(footer, 'Version')
        generator_version.text = '1.0'

        generation_date = ET.SubElement(footer, 'GenerationTimestamp')
        generation_date.text = datetime.now().isoformat()

        # 写入文件
        xml_str = minidom.parseString(ET.tostring(root, encoding='unicode')).toprettyxml(indent='  ')

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(xml_str)

        return filepath

    def generate_pdf_report(
        self,
        report_data: Dict,
        filename: str = None
    ) -> str:
        """
        生成 PDF 格式的合规报告

        参数:
            report_data: 包含报告数据的字典
            filename: 输出文件名（不含路径）

        返回:
            生成的 PDF 文件路径
        """

        if not HAS_REPORTLAB:
            raise ImportError("reportlab is required for PDF generation. Install with: pip install reportlab")

        if filename is None:
            filename = f"CBAM_Report_{uuid.uuid4().hex[:8]}.pdf"

        filepath = os.path.join(self.reports_dir, filename)

        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            rightMargin=20*mm,
            leftMargin=20*mm,
            topMargin=20*mm,
            bottomMargin=20*mm
        )

        styles = getSampleStyleSheet()
        story = []

        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=30,
            alignment=1  # Center
        )

        story.append(Paragraph('CarbonPass', title_style))
        story.append(Paragraph('CBAM Compliance Report', title_style))
        story.append(Spacer(1, 20))

        # Report info
        info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=10, spaceAfter=6)

        report_info = [
            f"<b>Report ID:</b> {report_data.get('report_id', 'N/A')}",
            f"<b>Report Date:</b> {datetime.now().strftime('%Y-%m-%d')}",
            f"<b>Reporting Period:</b> {report_data.get('reporting_period', 'N/A')}",
        ]
        for info in report_info:
            story.append(Paragraph(info, info_style))

        story.append(Spacer(1, 20))

        # Section: Company Information
        story.append(Paragraph('<b>1. Facility Information</b>', styles['Heading2']))
        company_data = [
            ['Company Name:', report_data.get('company_name', 'N/A')],
            ['Facility ID:', report_data.get('facility_id', 'N/A')],
            ['Address:', report_data.get('facility_address', 'N/A')],
            ['Country:', report_data.get('country', 'Malaysia')],
        ]
        t = Table(company_data, colWidths=[120, 350])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

        # Section: Production Data
        story.append(Paragraph('<b>2. Production Data</b>', styles['Heading2']))
        production_data = [
            ['Product Type:', report_data.get('product_type', 'N/A')],
            ['Quantity:', f"{report_data.get('quantity', 0)} {report_data.get('unit', 'tonnes')}"],
            ['Production Period:', report_data.get('production_period', 'N/A')],
        ]
        t = Table(production_data, colWidths=[120, 350])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

        # Section: Emissions Data
        story.append(Paragraph('<b>3. Carbon Emissions Data</b>', styles['Heading2']))
        emissions_data = [
            ['Scope 1 (Direct Emissions):', f"{report_data.get('scope1_kg', 0):.2f} kg CO2"],
            ['Scope 2 (Electricity):', f"{report_data.get('scope2_kg', 0):.2f} kg CO2"],
            ['Total Embedded Emissions:', f"{report_data.get('embedded_emissions_kg', 0):.2f} kg CO2"],
            ['Emissions per Tonne:', f"{report_data.get('emissions_per_tonne', 0):.3f} kg CO2/tonne"],
        ]
        t = Table(emissions_data, colWidths=[180, 290])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 2), (-1, 2), colors.lightgrey),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

        # Section: CBAM Certificates
        story.append(Paragraph('<b>4. CBAM Certificate Requirements</b>', styles['Heading2']))
        certificates = report_data.get('certificate_need', {})
        cert_data = [
            ['Total Emissions:', f"{certificates.get('total_emissions_tonne', 0):.3f} tonne CO2"],
            ['Certificates Needed:', f"{int(certificates.get('cbam_certificates_needed', 0))} certificates"],
            ['Cost (@ EUR {price}/tonne):'.format(price=certificates.get('price_per_certificate_eur', 85)),
             f"EUR {certificates.get('total_cost_eur', 0):.2f}"],
        ]
        t = Table(cert_data, colWidths=[180, 290])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 30))

        # Footer
        footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
        story.append(Paragraph(
            'This report was generated by CarbonPass. For verification and certification, please contact an accredited CBAM verifier.',
            footer_style
        ))
        story.append(Paragraph(
            f'Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | CarbonPass v1.0',
            footer_style
        ))

        doc.build(story)
        return filepath

    def generate_malaysia_mrv_xml(
        self,
        mrv_data: Dict,
        filename: str = None
    ) -> str:
        """
        生成马来西亚 MRV 格式的 XML 报告

        参数:
            mrv_data: MRV 数据字典
            filename: 输出文件名

        返回:
            生成的 XML 文件路径
        """

        if filename is None:
            filename = f"MY_MRV_{uuid.uuid4().hex[:8]}.xml"

        filepath = os.path.join(self.reports_dir, filename)

        root = ET.Element('MalaysiaMRVReport', {
            'xmlns': 'urn:my:mrv:v1.0',
            'version': '1.0'
        })

        # Header
        header = ET.SubElement(root, 'ReportHeader')
        report_id = ET.SubElement(header, 'ReportID')
        report_id.text = mrv_data.get('report_id', uuid.uuid4().hex[:12].upper())
        report_date = ET.SubElement(header, 'ReportDate')
        report_date.text = datetime.now().strftime('%Y-%m-%d')
        reporting_year = ET.SubElement(header, 'ReportingYear')
        reporting_year.text = str(mrv_data.get('reporting_year', datetime.now().year))

        # Facility
        facility = ET.SubElement(root, 'Facility')
        facility_name = ET.SubElement(facility, 'FacilityName')
        facility_name.text = mrv_data.get('facility_name', '')
        facility_id = ET.SubElement(facility, 'FacilityID')
        facility_id.text = mrv_data.get('facility_id', '')
        address = ET.SubElement(facility, 'Address')
        address.text = mrv_data.get('address', '')

        # Emissions totals
        totals = ET.SubElement(root, 'EmissionsTotals')
        scope1 = ET.SubElement(totals, 'Scope1Emissions', {'unit': 'kg CO2'})
        scope1.text = str(mrv_data.get('total_scope1_kg', 0))
        scope2 = ET.SubElement(totals, 'Scope2Emissions', {'unit': 'kg CO2'})
        scope2.text = str(mrv_data.get('total_scope2_kg', 0))
        total = ET.SubElement(totals, 'TotalEmissions', {'unit': 'kg CO2'})
        total.text = str(mrv_data.get('total_emissions_kg', 0))

        # Products breakdown
        products_elem = ET.SubElement(root, 'ProductsBreakdown')
        for product in mrv_data.get('products', []):
            prod = ET.SubElement(products_elem, 'Product')
            prod_type = ET.SubElement(prod, 'Type')
            prod_type.text = product.get('type', '')
            quantity = ET.SubElement(prod, 'Quantity')
            quantity.text = str(product.get('quantity', 0))
            emissions = ET.SubElement(prod, 'EmissionsKg')
            emissions.text = str(product.get('emissions_kg', 0))

        # Write
        xml_str = minidom.parseString(ET.tostring(root, encoding='unicode')).toprettyxml(indent='  ')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(xml_str)

        return filepath

    def list_reports(self) -> List[Dict]:
        """列出所有生成的报告"""
        reports = []
        for f in os.listdir(self.reports_dir):
            if f.endswith(('.xml', '.pdf')):
                filepath = os.path.join(self.reports_dir, f)
                stat = os.stat(filepath)
                reports.append({
                    'filename': f,
                    'filepath': filepath,
                    'size': stat.st_size,
                    'created': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'type': 'XML' if f.endswith('.xml') else 'PDF'
                })
        return sorted(reports, key=lambda x: x['created'], reverse=True)


def create_sample_reports():
    """创建示例报告"""

    generator = ReportGenerator()

    sample_data = {
        'report_id': 'CBAM-MY-2026-Q1-001',
        'reporting_period': '2026-Q1',
        'company_name': 'Steel Malaysia Sdn Bhd',
        'facility_id': 'MY-SM-2024-001',
        'facility_address': 'Lot 123, Industrial Zone, Klang, Selangor, Malaysia',
        'country': 'MY',
        'contact_person': 'Ahmad bin Ali',
        'contact_email': 'ahmad@steel.my',
        'product_type': 'Hot Rolled Steel Coil',
        'product_description': 'Hot rolled steel coils for export to EU',
        'quantity': 5000,
        'unit': 'tonnes',
        'production_period': '2026-01-01 to 2026-03-31',
        'scope1_kg': 2500000,
        'scope2_kg': 3500000,
        'embedded_emissions_kg': 6000000,
        'emissions_per_tonne': 1200,
        'methodology': 'Actual data based on facility measurements',
        'factor_source': 'Malaysian grid factor (0.7 kg/kWh) + IPCC AR6 fuel factors',
        'eu_importer': 'German Steel Importers GmbH',
        'import_country': 'DE',
        'import_quantity': 5000,
        'verified': False,
        'uncertainty_percent': 8.5,
        'data_quality': 'Measured',
        'certificate_need': {
            'total_emissions_tonne': 6000,
            'cbam_certificates_needed': 6000,
            'price_per_certificate_eur': 85,
            'total_cost_eur': 510000
        }
    }

    # Generate XML
    xml_path = generator.generate_cbam_xml(sample_data)
    print(f"Generated CBAM XML: {xml_path}")

    # Generate PDF
    try:
        pdf_path = generator.generate_pdf_report(sample_data)
        print(f"Generated PDF: {pdf_path}")
    except ImportError as e:
        print(f"PDF generation skipped: {e}")

    # Generate Malaysia MRV
    mrv_data = {
        'report_id': 'MRV-MY-2026-001',
        'facility_name': 'Steel Malaysia Sdn Bhd',
        'facility_id': 'MY-SM-2024-001',
        'address': 'Lot 123, Industrial Zone, Klang, Selangor',
        'reporting_year': 2026,
        'total_scope1_kg': 2500000,
        'total_scope2_kg': 3500000,
        'total_emissions_kg': 6000000,
        'products': [
            {'type': 'Hot Rolled Steel Coil', 'quantity': 5000, 'emissions_kg': 6000000}
        ]
    }
    mrv_path = generator.generate_malaysia_mrv_xml(mrv_data)
    print(f"Generated Malaysia MRV XML: {mrv_path}")

    print("\n=== All Reports ===")
    for r in generator.list_reports():
        print(f"  {r['filename']} ({r['size']} bytes) - {r['created']}")


if __name__ == '__main__':
    create_sample_reports()