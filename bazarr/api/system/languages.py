# coding=utf-8

from flask_restx import Resource, Namespace, reqparse
from itertools import chain
from operator import itemgetter

from app.database import TableHistory, TableHistoryMovie, TableSettingsLanguages, database, select
from languages.get_languages import alpha2_from_alpha3, language_from_alpha2, alpha3_from_alpha2
from languages.formatting import LanguageInfo

from ..utils import authenticate, False_Keys

api_ns_system_languages = Namespace('System Languages', description='Get languages list')


class MenuLanguage(LanguageInfo):
    enabled: bool


@api_ns_system_languages.route('system/languages')
class Languages(Resource):
    get_request_parser = reqparse.RequestParser()
    get_request_parser.add_argument('history', type=str, required=False, help='Language name for history stats')

    @authenticate
    @api_ns_system_languages.expect(get_request_parser)
    @api_ns_system_languages.response(200, 'Success')
    @api_ns_system_languages.response(401, 'Not Authenticated')
    def get(self) -> list[MenuLanguage]:
        """List languages for history filter or for language filter menu"""
        args = self.get_request_parser.parse_args()
        history = args.get('history')
        if history and history not in False_Keys:
            languages = chain.from_iterable(
                database.execute(statement).scalars() for statement in (
                    select(TableHistory.language).where(TableHistory.language.is_not(None)),
                    select(TableHistoryMovie.language).where(TableHistoryMovie.language.is_not(None)),
                )
            )
            languages_dicts: list[MenuLanguage] = []
            seen_codes: set[str | None] = set()
            for token in languages:
                language = token.split(':', 1)[0]
                code2 = None
                if len(language) == 2:
                    code2 = language
                elif len(language) == 3:
                    code2 = alpha2_from_alpha3(language)
                else:
                    continue

                if code2 not in seen_codes:
                    try:
                        languages_dicts.append({
                            'code2': code2,
                            'code3': alpha3_from_alpha2(code2),
                            'name': language_from_alpha2(code2),
                            # Compatibility: Use false temporarily
                            'enabled': False
                        })
                        seen_codes.add(code2)
                    except Exception:
                        continue
        else:
            languages_dicts = [{
                'name': x.name,
                'code2': x.code2,
                'code3': x.code3,
                'enabled': x.enabled == 1
            } for x in database.execute(
                select(TableSettingsLanguages.name,
                       TableSettingsLanguages.code2,
                       TableSettingsLanguages.code3,
                       TableSettingsLanguages.enabled)
                .order_by(TableSettingsLanguages.name))
                .all()]

        return sorted(languages_dicts, key=itemgetter('name'))
