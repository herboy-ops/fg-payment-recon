import os
import time
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib
from flask import send_file, abort
from flask import send_from_directory, abort
import logging
from flask import Flask, request, render_template, redirect, url_for, send_from_directory, session

matplotlib.use('Agg')

# Setup logging
logging.basicConfig(filename='logs/app.log', level=logging.INFO)

app = Flask(__name__)
app.secret_key = 'your_secret_key'

app.config['UPLOAD_FOLDER'] = 'uploads/'
app.config['RESULT_FOLDER'] = 'static/results/'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['RESULT_FOLDER'], exist_ok=True)
os.makedirs('logs', exist_ok=True)

ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def read_file(file_path):
    ext = file_path.rsplit('.', 1)[1].lower()
    try:
        if ext in ['xlsx', 'xls']:
            return pd.read_excel(file_path)
        elif ext == 'csv':
            return pd.read_csv(file_path)
        elif ext == 'txt':
            return pd.read_csv(file_path, delimiter='\t')
        else:
            return None
    except Exception as e:
        logging.error(f"Error reading {file_path}: {e}")
        return None

def clean_old_files(folder, days=3):
    now = time.time()
    for filename in os.listdir(folder):
        file_path = os.path.join(folder, filename)
        if os.path.isfile(file_path) and os.stat(file_path).st_mtime < now - days * 86400:
            os.remove(file_path)
            logging.info(f"Deleted old file: {file_path}")

@app.route('/')
def index():
    summary = session.get('summary', None)
    chart_file = session.get('chart_file', None)
    filename = session.get('filename', None)
    mismatches_file1 = session.get('mismatches_file1', None)
    mismatches_file2 = session.get('mismatches_file2', None)
    mode_wise_unmatched = session.get('mode_wise_unmatched', None)
    mode_wise_pivot = session.get('mode_wise_pivot', None)
    return render_template('index.html', summary=summary, chart_file=chart_file, filename=filename,
                           mismatches_file1=mismatches_file1, mismatches_file2=mismatches_file2,
                           mode_wise_unmatched=mode_wise_unmatched, mode_wise_pivot=mode_wise_pivot)

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'file1' not in request.files or 'file2' not in request.files or 'payment_type' not in request.form:
        return render_template('index.html', error='All fields are required.')

    file1 = request.files['file1']
    file2 = request.files['file2']
    payment_type = request.form['payment_type']

    if file1.filename == '' or file2.filename == '':
        return render_template('index.html', error='Please select both files before uploading.')

    if not allowed_file(file1.filename) or not allowed_file(file2.filename):
        return render_template('index.html', error='Only .xlsx, .xls, .csv, and .txt files are allowed.')

    file1_path = os.path.join(app.config['UPLOAD_FOLDER'], file1.filename)
    file2_path = os.path.join(app.config['UPLOAD_FOLDER'], file2.filename)
    try:
        file1.save(file1_path)
        file2.save(file2_path)
        logging.info(f"Files uploaded: {file1.filename}, {file2.filename}")
    except Exception as e:
        logging.error(f"Error saving files: {e}")
        return render_template('index.html', error='Error saving files.')

    clean_old_files(app.config['UPLOAD_FOLDER'])
    clean_old_files(app.config['RESULT_FOLDER'])

    output_filename, summary, chart_file, mismatch_html_1, mismatch_html_2, mode_wise_unmatched, mode_wise_pivot = process_files(file1_path, file2_path, payment_type)

    if output_filename:
        session['summary'] = summary
        session['chart_file'] = chart_file
        session['filename'] = output_filename
        session['mismatches_file1'] = mismatch_html_1
        session['mismatches_file2'] = mismatch_html_2
        session['mode_wise_unmatched'] = mode_wise_unmatched.to_html(classes='table table-bordered', index=False)
        session['mode_wise_pivot'] = mode_wise_pivot.to_html(classes='table table-bordered', index=False)
        return redirect(url_for('index'))
    else:
        return render_template('index.html', error='Error during file processing.')

def process_files(file1_path, file2_path, payment_type):
    df1 = read_file(file1_path)
    df2 = read_file(file2_path)

    if df1 is None or df2 is None:
        return None, None, None, None, None, None, None

    df1.columns = df1.columns.str.strip().str.lower()
    df2.columns = df2.columns.str.strip().str.lower()

    if payment_type in ['ATP', 'NEFT']:
        df1_column = 'utr no'
        df2_column = 'utr'
    else:
        df1_column = 'receipt no'
        df2_column = 'receipt no'

    if df1_column in df1.columns and df2_column in df2.columns:
        try:
            matched_data = pd.merge(df1, df2, left_on=df1_column, right_on=df2_column, how='inner')
            non_matching_data_df1 = df1[~df1[df1_column].isin(matched_data[df1_column])]
            non_matching_data_df2 = df2[~df2[df2_column].isin(matched_data[df2_column])]

            matched_count = len(matched_data)
            unmatched_count_file1 = len(non_matching_data_df1)
            unmatched_count_file2 = len(non_matching_data_df2)
            total_records_cis = len(df1)
            total_records_tp = len(df2)

            summary = {
                'Total CIS Records': total_records_cis,
                'Total TP Records': total_records_tp,
                'CIS = TP (Matched)': matched_count,
                'CIS <> TP (Mismatch from CIS)': unmatched_count_file1,
                'TP <> CIS (Mismatch from TP)': unmatched_count_file2
            }

            # Detect mode columns
            possible_mode_columns = ['payment mode', 'mode of payment', 'payment product code', 'mode']

            mode_column_cis = next((col for col in non_matching_data_df1.columns if col.strip().lower() in possible_mode_columns), None)
            mode_column_tp = next((col for col in non_matching_data_df2.columns if col.strip().lower() in possible_mode_columns), None)

            if mode_column_cis:
                mode_wise_unmatched_cis = non_matching_data_df1[mode_column_cis].value_counts().reset_index()
                mode_wise_unmatched_cis.columns = ['Payment Mode', ' Count']
            else:
                mode_wise_unmatched_cis = pd.DataFrame(columns=['Payment Mode', ' Count'])

            if mode_column_tp:
                mode_wise_unmatched_tp = non_matching_data_df2[mode_column_tp].value_counts().reset_index()
                mode_wise_unmatched_tp.columns = ['Payment Mode', ' Count']
            else:
                mode_wise_unmatched_tp = pd.DataFrame(columns=['Payment Mode', ' Count'])

            # Combine both CIS and TP mode-wise unmatched data
            mode_wise_unmatched_combined = pd.concat([mode_wise_unmatched_cis, mode_wise_unmatched_tp], axis=0, ignore_index=True)

            # Mode-wise pivot
            combined_df = pd.concat([df1, df2], ignore_index=True)
            combined_df.columns = combined_df.columns.str.strip()

            # Ensure 'Mode' column exists before renaming
            mode_columns = ["payment product code", "mode of payment", "payment mode"]
            for col in mode_columns:
                if col.lower() in combined_df.columns:
                    combined_df.rename(columns={col.lower(): "Mode"}, inplace=True)
                    break

            # Check if 'Mode' column exists before proceeding with the pivot
            if 'Mode' in combined_df.columns:
                mode_wise_pivot = pd.pivot_table(combined_df, index='Mode', aggfunc='size').reset_index()
                mode_wise_pivot.columns = ['Mode', 'Count']
            else:
                mode_wise_pivot = pd.DataFrame(columns=['Mode', 'Count'])

            # Chart generation
            chart_file = 'summary_chart.png'
            chart_path = os.path.join(app.config['RESULT_FOLDER'], chart_file)
            categories = ['Total CIS Records', 'Total TP Records', 'CIS = TP (Matched)', 'CIS <> TP (Mismatch from CIS)', 'TP <> CIS (Mismatch from TP)']
            counts = [summary[key] for key in categories]

            plt.figure(figsize=(8, 4))
            colors = ['#ff9999', '#66b3ff', '#99ff99', '#ffcc99', '#c2c2f0']
            plt.pie(counts, labels=[f'{cat}\n{val:,}' for cat, val in zip(categories, counts)],
                    colors=colors, startangle=90, counterclock=False, wedgeprops={'linewidth': 5, 'edgecolor': 'white'},
                    autopct='%1.1f%%', textprops={'color': 'black'})
            plt.title(f'{payment_type} Reconciliation Summary\nCounts and Matches', fontsize=14)
            center_circle = plt.Circle((0, 0), 0.70, fc='white')
            fig = plt.gcf()
            fig.gca().add_artist(center_circle)
            plt.tight_layout()
            plt.savefig(chart_path)
            plt.close()

            output_filename = f'{payment_type}_output.xlsx'
            output_file = os.path.join(app.config['RESULT_FOLDER'], output_filename)
            with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
                matched_data.to_excel(writer, sheet_name='Matched', index=False)
                non_matching_data_df1.to_excel(writer, sheet_name='total_records_cis', index=False)
                non_matching_data_df2.to_excel(writer, sheet_name='total_records_tp', index=False)
                mode_wise_unmatched_combined.to_excel(writer, sheet_name='Mode_Wise_Unmatched', index=False)
                mode_wise_pivot.to_excel(writer, sheet_name='Mode_Wise_Pivot', index=False)

            mismatch_html_1 = non_matching_data_df1.head(10).to_html(classes='table table-striped', index=False)
            mismatch_html_2 = non_matching_data_df2.head(10).to_html(classes='table table-striped', index=False)

            logging.info(f"Reconciliation complete for {payment_type}, saved to {output_filename}")
            return output_filename, summary, chart_file, mismatch_html_1, mismatch_html_2, mode_wise_unmatched_combined, mode_wise_pivot

        except Exception as e:
            logging.error(f"Error during processing: {e}")
            return None, None, None, None, None, None, None
    else:
        logging.error(f"Column mismatch: {df1_column} or {df2_column} not found")
        return None, None, None, None, None, None, None

@app.route('/download/<filename>')
def download_file(filename):
    file_path = os.path.join(app.config['RESULT_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_from_directory(app.config['RESULT_FOLDER'], filename, as_attachment=True)
    else:
        return f"Error: {filename} does not exist."
    
@app.route('/download-sample/<filename>')
def download_sample(filename):
    try:
        # Absolute path to static/samples
        sample_dir = os.path.join(app.root_path, 'static', 'samples')

        if os.path.exists(os.path.join(sample_dir, filename)):
            return send_from_directory(sample_dir, filename, as_attachment=True)
        else:
            return f"{filename} not found.", 404
    except Exception as e:
        return f"Error: {str(e)}", 500


if __name__ == "__main__":
    app.run(debug=True)