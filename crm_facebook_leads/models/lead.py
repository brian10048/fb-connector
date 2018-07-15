# -*- coding: utf-8 -*-

from odoo import models, fields, api
import requests


class CrmFacebookPage(models.Model):
    _name = 'crm.facebook.page'

    name = fields.Char(required=True)
    access_token = fields.Char(required=True, string='Page Access Token')
    form_ids = fields.One2many('crm.facebook.form', 'page_id', string='Lead Forms')

    @api.multi
    def get_forms(self):
        r = requests.get("https://graph.facebook.com/v2.12/" + self.name + "/leadgen_forms", params = {'access_token': self.access_token}).json()
        for form in r['data']:
            if not self.form_ids.filtered(lambda f: f.facebook_form_id == form['id']):
                self.env['crm.facebook.form'].create({
                                                'name': form['name'],
                                                'facebook_form_id': form['id'],
                                                'page_id': self.id
                                             }).get_fields()

class CrmFacebookForm(models.Model):
    _name = 'crm.facebook.form'

    name = fields.Char(required=True)
    facebook_form_id = fields.Char(required=True, string='Form ID')
    access_token = fields.Char(required=True, related='page_id.access_token', string='Page Access Token')
    page_id = fields.Many2one('crm.facebook.page', readonly=True, ondelete='cascade', string='Facebook Page')
    mappings = fields.One2many('crm.facebook.form.field', 'form_id')
    team_id = fields.Many2one('crm.team', domain=['|', ('use_leads', '=', True), ('use_opportunities', '=', True)], string="Sales Team")
    campaign_id = fields.Many2one('utm.campaign')
    source_id = fields.Many2one('utm.source')
    medium_id = fields.Many2one('utm.medium')

    def get_fields(self):
        self.mappings.unlink()
        r = requests.get("https://graph.facebook.com/v2.12/" + self.facebook_form_id, params = {'access_token': self.access_token, 'fields': 'qualifiers'}).json()
        if r.get('qualifiers'):
            for qualifier in r.get('qualifiers'):
                self.env['crm.facebook.form.field'].create({
                                                                'form_id': self.id,
                                                                'name': qualifier['label'],
                                                                'facebook_field': qualifier['field_key']
                                                            })

class CrmFacebookFormField(models.Model):
    _name = 'crm.facebook.form.field'

    form_id = fields.Many2one('crm.facebook.form', required=True, ondelete='cascade', string='Form')
    name = fields.Char()
    odoo_field = fields.Many2one('ir.model.fields',
                                 domain=[('model', '=', 'crm.lead'),
                                         ('store', '=', True),
                                         ('ttype', 'in', ('char',
                                                          'date',
                                                          'datetime',
                                                          'float',
                                                          'html',
                                                          'integer',
                                                          'monetary',
                                                          'many2one',
                                                          'selection',
                                                          'phone',
                                                          'text'))],
                                 required=False)
    facebook_field = fields.Char(required=True)

    _sql_constraints = [
                        ('field_unique', 'unique(form_id, odoo_field, facebook_field)', 'Mapping must be unique per form')
    ]
class CrmLead(models.Model):
    _inherit = 'crm.lead'

    facebook_lead_id = fields.Char('Lead ID')
    facebook_page_id = fields.Many2one('crm.facebook.page', related='facebook_form_id.page_id', store=True, string='Page', readonly=True)
    facebook_form_id = fields.Many2one('crm.facebook.form', string='Form')

    _sql_constraints = [
                        ('facebook_lead_unique', 'unique(facebook_lead_id)', 'This Facebook lead already exists!')
    ]

    def prepare_lead_creation(self, lead, form):
        vals, notes = self.get_fields_from_data(lead, form)
        vals.update({
            'facebook_lead_id': lead['id'],
            'name': self.get_opportunity_name(vals, lead, form),
            'description': "\n".join(notes),
            'team_id': form.team_id and form.team_id.id,
            'campaign_id': form.campaign_id and form.campaign_id.id,
            'source_id': form.source_id and form.source_id.id,
            'medium_id': form.medium_id and form.medium_id.id,
            'facebook_form_id': form.id,
            'date_open': lead['created_time'].split('+')[0].replace('T', ' ')
        })
        return vals

    def lead_creation(self, lead, form):
        vals = self.prepare_lead_creation(lead, form)
        lead_id = self.create(vals)
        self.env.cr.commit()
        return lead_id

    def get_opportunity_name(self, vals, lead, form):
        if not vals.get('name'):
            name = '%s - %s' % (form.name, lead['id'])
        else:
            name = vals.get('name')
        return name

    def get_fields_from_data(self, lead, form):
        vals, notes = {}, []
        form_mapping = form.mappings.filtered(lambda m: m.odoo_field).mapped('facebook_field')
        unmapped_fields = []
        for name, value in lead.items():
            if name not in form_mapping:
                unmapped_fields.append((name, value))
                continue
            odoo_field = form.mappings.filtered(lambda m: m.facebook_field == name).odoo_field
            notes.append('%s: %s' % (odoo_field.field_description, value))
            if odoo_field.ttype == 'many2one':
                related_value = self.env[odoo_field.relation].search([('display_name', '=', value)])
                vals.update({odoo_field.name: related_value and related_value.id})
            elif odoo_field.ttype in ('float', 'monetary'):
                vals.update({odoo_field.name: float(value)})
            elif odoo_field.ttype == 'integer':
                vals.update({odoo_field.name: int(value)})
            # TODO: separate date & datetime into two different conditionals
            elif odoo_field.ttype in ('date', 'datetime'):
                vals.update({odoo_field.name: value.split('+')[0].replace('T', ' ')})
            elif odoo_field.ttype == 'selection':
                vals.update({odoo_field.name: value})
            elif odoo_field.ttype == 'boolean':
                vals.update({odoo_field.name: value == 'true' if value else False})
            else:
                vals.update({odoo_field.name: value})

        # NOTE: Doing this to put unmapped fields at the end of the description
        for name, value in unmapped_fields:
            notes.append('%s: %s' % (name, value))

        return vals, notes

    def process_lead_field_data(self, lead):
        field_data = lead.pop('field_data')
        lead_data = dict(lead)
        lead_data.update([(l['name'], l['values'][0])
                          for l in field_data
                          if l.get('name') and l.get('values')])
        return lead_data

    def lead_processing(self, r, form):
        if not r.get('data'):
            return
        for lead in r['data']:
            lead = self.process_lead_field_data(lead)
            if not self.search([('facebook_lead_id', '=', lead.get('id')), '|', ('active', '=', True), ('active', '=', False)]):
                self.lead_creation(lead, form)
        if r.get('paging') and r['paging'].get('next'):
            self.lead_processing(requests.get(r['paging']['next']).json(), form)
        return

    @api.model
    def get_facebook_leads(self):
        # /!\ TODO: Add this URL as a configuration setting in the company
        fb_api = "https://graph.facebook.com/v2.12/"
        for form in self.env['crm.facebook.form'].search([]):
            # /!\ NOTE: We have to try lead creation if it fails we just log it into the Lead Form?
            r = requests.get(fb_api + form.facebook_form_id + "/leads", params = {'access_token': form.access_token, 'fields': 'created_time,field_data,ad_id,ad_name,campaign_id,campaign_name'}).json()
            self.lead_processing(r, form)
